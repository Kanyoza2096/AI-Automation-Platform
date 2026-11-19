"""Focused authentication regression for atomic Director mission-task approval."""

import hashlib
import hmac
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient


def test_mission_task_approval_uses_signed_kcpg_actor_not_body_actor(monkeypatch):
    from services.director import Director, create_app
    from services.director.models import Authority, Risk, Sector, Status, Task

    secret = "test-kcpg-context-secret-that-is-long-enough"
    monkeypatch.setenv("MASTER_API_TOKEN", "master-test-token")
    monkeypatch.setenv("PYTHON_SERVICE_TOKEN", "service-test-token")
    monkeypatch.setenv("KCPG_CONTEXT_SIGNING_SECRET", secret)
    monkeypatch.setenv("KDO_DIRECTOR_MISSION_RUNTIME_ENABLED", "true")

    director = Director(supabase_client=None)
    task = Task(
        id="mission-approval-auth-task",
        sector=Sector.CONTENT,
        description="mission approval actor boundary",
        capability="self_diagnostic",
        authority=Authority.SUGGEST,
        risk=Risk.LOW,
        status=Status.APPROVAL,
        workspace_id="ws-auth",
        mission_id="mission-auth",
    )
    director.tasks[task.id] = task
    app = create_app(director)

    @app.middleware("http")
    async def service_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-auth",
            principal_id="kcpg-service",
            role="species",
            species_privileges=True,
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    class FakeStore:
        async def get_workspace_task(self, *, task_id, workspace_id, mission_id):
            assert (task_id, workspace_id, mission_id) == (
                task.id, "ws-auth", "mission-auth"
            )
            return {
                "id": task.id,
                "status": "approved",
                "payload": {
                    "status": "approved",
                    "approved_by": "signed-human-operator",
                    "approved_at": "2026-11-19T19:30:00+00:00",
                },
            }

    class FakeRuntime:
        store = FakeStore()

        def __init__(self):
            self.calls = []

        async def approve_task(self, **kwargs):
            self.calls.append(kwargs)

    runtime = FakeRuntime()
    app.state.director_mission_runtime = runtime

    timestamp = str(int(time.time() * 1000))
    request_id = "mission-approval-auth-request"
    actor_id = "signed-human-operator"
    actor_role = "operator"
    canonical = "|".join((timestamp, request_id, "ws-auth", actor_id, actor_role))
    signature = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()

    response = TestClient(app).post(
        f"/tasks/{task.id}/approve",
        headers={
            "Authorization": "Bearer valid-user-session",
            "X-Workspace-ID": "ws-auth",
            "X-Correlation-ID": request_id,
            "X-KCPG-Context-Timestamp": timestamp,
            "X-KCPG-Context-Request-ID": request_id,
            "X-KCPG-Context-Workspace-ID": "ws-auth",
            "X-KCPG-Context-Actor-ID": actor_id,
            "X-KCPG-Context-Actor-Role": actor_role,
            "X-KCPG-Context-Signature": signature,
        },
        json={"actor": "attacker-controlled-body-actor"},
    )

    assert response.status_code == 200, response.text
    assert len(runtime.calls) == 1
    assert runtime.calls[0]["operator_id"] == actor_id
    assert runtime.calls[0]["workspace_id"] == "ws-auth"
    assert response.json()["approved_by"] == actor_id
    assert task.approved_by == actor_id
    assert task.approved_at.isoformat() == "2026-11-19T19:30:00+00:00"
