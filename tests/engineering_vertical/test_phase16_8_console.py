from __future__ import annotations

import hashlib
import hmac
import os
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.dependencies import auth_user_email_var, auth_workspace_id_var, require_auth, require_workspace_operator
from routes.kdo_engineering_console import router

TEST_CONTEXT_SECRET = "engineering-console-test-context-secret-long-enough"
os.environ["KCPG_CONTEXT_SIGNING_SECRET"] = TEST_CONTEXT_SECRET

def signed_gateway_context(workspace_id="workspace-2", actor_id="reviewer-42", actor_role="operator"):
    timestamp = str(int(time.time() * 1000))
    request_id = "test-request-123"
    canonical = "|".join((timestamp, request_id, workspace_id, actor_id, actor_role))
    signature = hmac.new(TEST_CONTEXT_SECRET.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return {
        "x-kcpg-context-timestamp": timestamp,
        "x-kcpg-context-request-id": request_id,
        "x-kcpg-context-workspace-id": workspace_id,
        "x-kcpg-context-actor-id": actor_id,
        "x-kcpg-context-actor-role": actor_role,
        "x-kcpg-context-signature": signature,
    }


class FakeEngineeringConsoleProvider:
    def __init__(self):
        self.calls = []
        self.tasks = [{"task_id": "task-1", "workspace_id": "workspace-1", "objective": "Build a test app", "status": "awaiting_review", "revision": 3}]
        self.events = [{"event_id": "e1", "sequence_no": 1, "event_type": "agent_message", "actor_id": "agent-frontend", "created_at": "2026-10-29T08:00:00Z", "payload": {"agent_name": "Frontend Engineer", "message": "Implementing the settings page"}}]

    def capabilities(self, *, workspace_id):
        self.calls.append(("capabilities", workspace_id))
        return {"runtime_connected": True, "live_events": True, "workspace_id": workspace_id}

    def list_tasks(self, *, workspace_id, limit):
        self.calls.append(("list_tasks", workspace_id, limit))
        return {"tasks": [t for t in self.tasks if t["workspace_id"] == workspace_id][:limit]}

    def submit_task(self, *, workspace_id, user_id, objective, acceptance_criteria, idempotency_key):
        self.calls.append(("submit_task", workspace_id, user_id, objective, acceptance_criteria, idempotency_key))
        task = {"task_id": "task-new", "workspace_id": workspace_id, "objective": objective, "status": "proposed", "revision": 1}
        self.tasks.append(task)
        return task

    def get_task(self, *, task_id, workspace_id):
        self.calls.append(("get_task", task_id, workspace_id))
        return next((t for t in self.tasks if t["task_id"] == task_id and t["workspace_id"] == workspace_id), None)

    def list_events(self, *, task_id, workspace_id, after_sequence, limit):
        self.calls.append(("list_events", task_id, workspace_id, after_sequence, limit))
        return {"events": self.events if task_id == "task-1" and workspace_id == "workspace-1" else []}

    def submit_plan(self, *, task_id, workspace_id, user_id, steps):
        self.calls.append(("submit_plan", task_id, workspace_id, user_id, steps))
        task = self.get_task(task_id=task_id, workspace_id=workspace_id)
        task["revision"] += 1
        task["approval_required"] = True
        task["evidence"] = {"plan_review": {"state": "submitted", "steps": steps, "submitted_by": user_id}}
        return task

    def decide(self, *, task_id, workspace_id, user_id, decision, expected_revision):
        self.calls.append(("decide", task_id, workspace_id, user_id, decision, expected_revision))
        return {"task_id": task_id, "decision": decision, "status": "decision_recorded", "revision": expected_revision + 1}


def make_client(provider=None):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = lambda: True
    app.dependency_overrides[require_workspace_operator] = lambda: True
    if provider is not None:
        app.state.kdo_engineering_console_provider = provider

    @app.middleware("http")
    async def test_request_scope(request, call_next):
        ws = auth_workspace_id_var.set(request.headers.get("x-test-workspace", "workspace-1"))
        user = auth_user_email_var.set(request.headers.get("x-test-user", "operator@example.test"))
        try:
            return await call_next(request)
        finally:
            auth_workspace_id_var.reset(ws)
            auth_user_email_var.reset(user)

    return TestClient(app)


def test_console_html_is_served_and_uses_live_provider_not_mock_data():
    client = make_client(FakeEngineeringConsoleProvider())
    response = client.get("/engineering/console")
    assert response.status_code == 200
    assert "AI Software Engineering" in response.text
    assert "No simulated tasks or agent messages" in response.text
    assert "setInterval(refreshAll,2500)" in response.text


def test_provider_missing_fails_closed_instead_of_showing_fake_activity():
    client = make_client()
    response = client.get("/api/v1/engineering/console/tasks")
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "engineering_runtime_not_connected"


def test_task_list_is_workspace_scoped_and_provider_is_explicit():
    provider = FakeEngineeringConsoleProvider()
    client = make_client(provider)
    response = client.get("/api/v1/engineering/console/tasks", headers={"x-test-workspace": "workspace-1"})
    assert response.status_code == 200
    assert [t["task_id"] for t in response.json()["tasks"]] == ["task-1"]
    assert provider.calls[0] == ("list_tasks", "workspace-1", 30)


def test_task_submission_validates_and_delegates_to_provider():
    provider = FakeEngineeringConsoleProvider()
    client = make_client(provider)
    response = client.post("/api/v1/engineering/console/tasks", headers={"x-test-workspace": "workspace-1", "x-test-user": "operator@example.test"}, json={
        "objective": "Build a task tracker", "acceptance_criteria": ["Users can create tasks"],
        "idempotency_key": "request-1",
    })
    assert response.status_code == 202
    assert response.json()["task_id"] == "task-new"
    assert provider.calls[-1][0:5] == ("submit_task", "workspace-1", "operator@example.test", "Build a task tracker", ["Users can create tasks"])


def test_event_feed_returns_real_provider_events_scoped_to_workspace():
    provider = FakeEngineeringConsoleProvider()
    client = make_client(provider)
    response = client.get("/api/v1/engineering/console/tasks/task-1/events?after_sequence=0", headers={"x-test-workspace": "workspace-1"})
    assert response.status_code == 200
    assert response.json()["events"][0]["payload"]["message"] == "Implementing the settings page"


def test_approval_decision_requires_revision_and_delegates_to_policy_provider():
    provider = FakeEngineeringConsoleProvider()
    client = make_client(provider)
    response = client.post("/api/v1/engineering/console/tasks/task-1/decision", headers={"x-test-workspace": "workspace-1", "x-test-user": "reviewer@example.test"}, json={
        "decision": "approve", "expected_revision": 3,
    })
    assert response.status_code == 200
    assert response.json()["status"] == "decision_recorded"
    assert provider.calls[-1] == ("decide", "task-1", "workspace-1", "reviewer@example.test", "approve", 3)


def test_approval_rejects_missing_or_invalid_revision():
    client = make_client(FakeEngineeringConsoleProvider())
    response = client.post("/api/v1/engineering/console/tasks/task-1/decision", headers={"x-test-workspace": "workspace-1"}, json={"decision": "approve"})
    assert response.status_code == 422
    assert response.json()["detail"] == "expected_revision_required"


def test_plan_submission_is_validated_and_delegated_to_provider():
    provider = FakeEngineeringConsoleProvider()
    client = make_client(provider)
    response = client.post("/api/v1/engineering/console/tasks/task-1/plan", headers={"x-test-workspace": "workspace-1", "x-test-user": "planner@example.test"}, json={"steps": ["Inspect repository", "Implement change", "Run tests"]})
    assert response.status_code == 200
    assert response.json()["approval_required"] is True
    assert ("submit_plan", "task-1", "workspace-1", "planner@example.test", ["Inspect repository", "Implement change", "Run tests"]) in provider.calls


def test_plan_submission_rejects_empty_steps():
    client = make_client(FakeEngineeringConsoleProvider())
    response = client.post("/api/v1/engineering/console/tasks/task-1/plan", headers={"x-test-workspace": "workspace-1"}, json={"steps": []})
    assert response.status_code == 422


def test_signed_gateway_context_overrides_service_identity_workspace_and_actor():
    provider = FakeEngineeringConsoleProvider()
    client = make_client(provider)
    headers = signed_gateway_context(workspace_id="workspace-2", actor_id="real-operator-42")
    response = client.post("/api/v1/engineering/console/tasks", headers=headers, json={
        "objective": "Use gateway identity", "acceptance_criteria": ["Actor is attributed correctly"]
    })
    assert response.status_code == 202
    assert provider.calls[-1][1] == "workspace-2"
    assert provider.calls[-1][2] == "real-operator-42"


def test_tampered_signed_gateway_context_is_rejected():
    provider = FakeEngineeringConsoleProvider()
    client = make_client(provider)
    headers = signed_gateway_context(workspace_id="workspace-2", actor_id="real-operator-42")
    headers["x-kcpg-context-workspace-id"] = "workspace-evil"
    response = client.get("/api/v1/engineering/console/capabilities", headers=headers)
    assert response.status_code == 403
