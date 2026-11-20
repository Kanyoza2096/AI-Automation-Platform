from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]


def _build_director_app(monkeypatch):
    monkeypatch.setenv("MASTER_API_TOKEN", "master-test-token")
    monkeypatch.setenv("PYTHON_SERVICE_TOKEN", "service-test-token")
    from services.director import Director, create_app
    director = Director(supabase_client=None)
    return create_app(director)


def test_director_reuses_canonical_request_context(monkeypatch):
    app = _build_director_app(monkeypatch)

    @app.middleware("http")
    async def canonical_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-test",
            role="viewer",
            species_privileges=False,
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    client = TestClient(app)
    response = client.get("/status", headers={"Authorization": "Bearer valid-user-session"})
    assert response.status_code == 200


def test_director_rejects_unknown_direct_token(monkeypatch):
    app = _build_director_app(monkeypatch)
    client = TestClient(app)
    response = client.get("/status", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401


def test_director_reports_missing_internal_auth_config_as_503(monkeypatch):
    monkeypatch.delenv("MASTER_API_TOKEN", raising=False)
    monkeypatch.delenv("PYTHON_SERVICE_TOKEN", raising=False)
    from services.director import Director, create_app
    app = create_app(Director(supabase_client=None))
    client = TestClient(app)
    response = client.get("/status", headers={"Authorization": "Bearer arbitrary-token"})
    assert response.status_code == 503


def test_director_viewer_cannot_mutate_with_canonical_context(monkeypatch):
    app = _build_director_app(monkeypatch)

    @app.middleware("http")
    async def canonical_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-test",
            role="viewer",
            species_privileges=False,
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    client = TestClient(app)
    response = client.post("/observe", headers={"Authorization": "Bearer valid-user-session"})
    assert response.status_code == 403


def test_director_rejects_workspace_context_without_authenticated_identity(monkeypatch):
    app = _build_director_app(monkeypatch)

    @app.middleware("http")
    async def incomplete_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-test", role="owner", species_privileges=True
        )
        # Deliberately omit auth_identity: a context alone must not authenticate.
        return await call_next(request)

    client = TestClient(app)
    response = client.get("/status", headers={"Authorization": "Bearer valid-user-session"})
    assert response.status_code == 401


def test_director_rejects_invalid_canonical_identity(monkeypatch):
    app = _build_director_app(monkeypatch)

    @app.middleware("http")
    async def invalid_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-test", role="owner", species_privileges=True
        )
        request.state.auth_identity = SimpleNamespace(valid=False)
        return await call_next(request)

    client = TestClient(app)
    response = client.get("/status", headers={"Authorization": "Bearer valid-user-session"})
    assert response.status_code == 401


def _workspace_context_app(monkeypatch, workspace_id="ws-a", role="owner"):
    app = _build_director_app(monkeypatch)

    @app.middleware("http")
    async def canonical_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id=workspace_id,
            principal_id="trusted-user-123",
            role=role,
            species_privileges=False,
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    return app


def test_director_proposals_are_filtered_to_authenticated_workspace(monkeypatch):
    from services.director.models import Proposal, ProposalStatus, Risk, Sector

    from services.director import Director, create_app
    d = Director(supabase_client=None)
    app = create_app(d)

    @app.middleware("http")
    async def tenant_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-a", principal_id="trusted-user-123", role="owner", species_privileges=False
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    def proposal(pid, workspace):
        return Proposal(
            id=pid, workspace_id=workspace, sector=Sector.CONTENT, title=pid,
            rationale="test", capability="self_diagnostic", params={}, target_param_value=None,
            expected_benefit="test", estimated_cost="0", risk=Risk.LOW,
            confidence=0.9, signal_ids=[], status=ProposalStatus.PENDING,
        )

    d.proposals["proposal-a"] = proposal("proposal-a", "ws-a")
    d.proposals["proposal-b"] = proposal("proposal-b", "ws-b")
    client = TestClient(app)

    listed = client.get("/proposals", headers={"Authorization": "Bearer valid-user-session"})
    assert listed.status_code == 200
    assert [p["id"] for p in listed.json()["proposals"]] == ["proposal-a"]

    foreign = client.get("/proposals/proposal-b", headers={"Authorization": "Bearer valid-user-session"})
    assert foreign.status_code == 404


def test_director_proposal_approval_uses_trusted_actor_and_hides_foreign_workspace(monkeypatch):
    from services.director.models import Proposal, ProposalStatus, Risk, Sector
    from services.director import Director, create_app

    d = Director(supabase_client=None)
    app = create_app(d)

    @app.middleware("http")
    async def tenant_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-a", principal_id="trusted-user-123", role="owner", species_privileges=False
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    d.proposals["foreign"] = Proposal(
        id="foreign", workspace_id="ws-b", sector=Sector.CONTENT, title="foreign",
        rationale="test", capability="self_diagnostic", params={}, target_param_value=None,
        expected_benefit="test", estimated_cost="0", risk=Risk.LOW,
        confidence=0.9, signal_ids=[], status=ProposalStatus.PENDING,
    )
    response = TestClient(app).post(
        "/proposals/foreign/approve",
        headers={"Authorization": "Bearer valid-user-session"},
        json={"actor": "attacker-supplied-name"},
    )
    assert response.status_code == 404
    assert d.proposals["foreign"].approved_by is None


def test_director_task_list_hides_unscoped_and_foreign_workspace_tasks(monkeypatch):
    from services.director import Director, create_app
    from services.director.models import Proposal, ProposalStatus, Risk, Sector, Task, Authority, Status

    d = Director(supabase_client=None)
    app = create_app(d)

    @app.middleware("http")
    async def tenant_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-a", principal_id="trusted-user-123", role="owner", species_privileges=False
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    def proposal(pid, workspace):
        return Proposal(
            id=pid, workspace_id=workspace, sector=Sector.CONTENT, title=pid,
            rationale="test", capability="self_diagnostic", params={}, target_param_value=None,
            expected_benefit="test", estimated_cost="0", risk=Risk.LOW,
            confidence=0.9, signal_ids=[], status=ProposalStatus.PENDING,
        )

    d.proposals["pa"] = proposal("pa", "ws-a")
    d.proposals["pb"] = proposal("pb", "ws-b")
    def task(tid, proposal_id=None):
        return Task(
            id=tid, sector=Sector.CONTENT, description="test", capability="self_diagnostic",
            authority=Authority.AUTO, risk=Risk.LOW, status=Status.PLANNED,
            proposal_id=proposal_id,
        )

    d.tasks["own"] = task("own", "pa")
    d.tasks["foreign"] = task("foreign", "pb")
    d.tasks["unscoped"] = task("unscoped")
    response = TestClient(app).get("/tasks", headers={"Authorization": "Bearer valid-user-session"})
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == ["own"]


def test_workspace_owner_cannot_mutate_platform_wide_director_state(monkeypatch):
    app = _workspace_context_app(monkeypatch, "ws-a", role="owner")
    client = TestClient(app)
    headers = {"Authorization": "Bearer valid-user-session"}

    assert client.post("/observe", headers=headers).status_code == 403
    assert client.post("/capabilities/example/toggle", headers=headers).status_code == 403
    assert client.post("/capabilities/example/execute", headers=headers, json={"params": {}}).status_code == 403


def test_director_proposal_approval_defers_execution_without_governed_handoff(monkeypatch):
    from services.director.models import Proposal, ProposalStatus, Risk, Sector, Status
    from services.director import Director, create_app

    d = Director(supabase_client=None)
    app = create_app(d)

    @app.middleware("http")
    async def tenant_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-a", principal_id="trusted-user-123", role="owner", species_privileges=False
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    d.proposals["local"] = Proposal(
        id="local", workspace_id="ws-a", sector=Sector.CONTENT, title="local",
        rationale="test", capability="self_diagnostic", params={}, target_param_value=None,
        expected_benefit="test", estimated_cost="0", risk=Risk.LOW,
        confidence=0.9, signal_ids=[], status=ProposalStatus.PENDING,
    )

    async def forbidden_legacy_execution(task_id):
        raise AssertionError("proposal approval must not invoke legacy execution")

    d._execute_async = forbidden_legacy_execution
    response = TestClient(app).post(
        "/proposals/local/approve",
        headers={"Authorization": "Bearer valid-user-session"},
        json={"actor": "untrusted-body-actor"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["execution"]["status"] == "deferred"
    assert payload["execution"]["reason"] == "awaiting_governed_scheduler_handoff"
    assert payload["proposal"]["approved_by"] == "trusted-user-123"
    assert payload["task"]["status"] == Status.APPROVED.value


def test_legacy_scheduler_cannot_execute_approved_proposal_task(monkeypatch):
    import asyncio
    from services.director import Director
    from services.director.models import Proposal, ProposalStatus, Risk, Sector, Status

    d = Director(supabase_client=None)
    d.proposals["scheduler-guard"] = Proposal(
        id="scheduler-guard", workspace_id="ws-a", sector=Sector.CONTENT,
        title="must not run", rationale="test", capability="self_diagnostic",
        params={}, target_param_value=None, expected_benefit="test",
        estimated_cost="0", risk=Risk.LOW, confidence=0.9, signal_ids=[],
        status=ProposalStatus.PENDING,
    )
    task = d.approve_proposal("scheduler-guard", actor="trusted-test-actor")
    task.status = Status.APPROVED
    calls = []

    async def forbidden_handler(params):
        calls.append(params)
        return {"ok": True}

    d.capabilities[task.capability].handler = forbidden_handler
    result = asyncio.run(d._execute_async(task.id))

    assert calls == []
    assert result.status == Status.APPROVAL
    assert result.error == "canonical_governed_handoff_required"
    assert result.result["status"] == "deferred"


def test_proposal_scheduler_uses_governed_handoff_without_legacy_fallback():
    import asyncio
    from services.director import Director
    from services.director.models import Proposal, ProposalStatus, Risk, Sector, Status

    d = Director(supabase_client=None)
    d.proposals["governed-once"] = Proposal(
        id="governed-once", workspace_id="ws-a", sector=Sector.CONTENT,
        title="governed action", rationale="test", capability="self_diagnostic",
        params={}, target_param_value=None, expected_benefit="test",
        estimated_cost="0", risk=Risk.LOW, confidence=0.9, signal_ids=[],
        status=ProposalStatus.PENDING,
    )
    task = d.approve_proposal("governed-once", actor="trusted-actor")
    task.status = Status.APPROVED
    calls = []

    async def governed_handoff(*, task, proposal, actor):
        calls.append((task.id, proposal.id, actor))
        return {"ok": True, "status": "succeeded", "execution_id": "exec-123", "intent_id": "act-123"}

    async def forbidden_legacy_handler(params):
        raise AssertionError("legacy handler must never run for proposal-backed tasks")

    d.capabilities[task.capability].governed_handoff = governed_handoff
    d.capabilities[task.capability].handler = forbidden_legacy_handler

    result = asyncio.run(d._execute_async(task.id))
    assert calls == [(task.id, "governed-once", "trusted-actor")]
    assert result.status == Status.COMPLETED
    assert result.result["execution_id"] == "exec-123"
    assert d.proposals["governed-once"].status == ProposalStatus.EXECUTED


def test_proposal_scheduler_keeps_ambiguous_governed_result_pending():
    import asyncio
    from services.director import Director
    from services.director.models import Proposal, ProposalStatus, Risk, Sector, Status

    d = Director(supabase_client=None)
    d.proposals["governed-ambiguous"] = Proposal(
        id="governed-ambiguous", workspace_id="ws-a", sector=Sector.CONTENT,
        title="governed action", rationale="test", capability="self_diagnostic",
        params={}, target_param_value=None, expected_benefit="test",
        estimated_cost="0", risk=Risk.LOW, confidence=0.9, signal_ids=[],
        status=ProposalStatus.PENDING,
    )
    task = d.approve_proposal("governed-ambiguous", actor="trusted-actor")
    task.status = Status.APPROVED

    async def ambiguous_handoff(*, task, proposal, actor):
        return {"ok": False, "status": "approval_required", "execution_id": ""}

    d.capabilities[task.capability].governed_handoff = ambiguous_handoff
    result = asyncio.run(d._execute_async(task.id))
    assert result.status == Status.APPROVAL
    assert result.error == "governed_handoff_not_confirmed:approval_required"
    assert d.proposals["governed-ambiguous"].status == ProposalStatus.APPROVED


def test_proposal_scheduler_does_not_claim_success_without_canonical_receipt_ids():
    import asyncio
    from services.director import Director
    from services.director.models import Proposal, ProposalStatus, Risk, Sector, Status

    d = Director(supabase_client=None)
    d.proposals["governed-no-receipt"] = Proposal(
        id="governed-no-receipt", workspace_id="ws-a", sector=Sector.CONTENT,
        title="governed action", rationale="test", capability="self_diagnostic",
        params={}, target_param_value=None, expected_benefit="test",
        estimated_cost="0", risk=Risk.LOW, confidence=0.9, signal_ids=[],
        status=ProposalStatus.PENDING,
    )
    task = d.approve_proposal("governed-no-receipt", actor="trusted-actor")
    task.status = Status.APPROVED

    async def receiptless_handoff(*, task, proposal, actor):
        return {"ok": True, "status": "succeeded", "output": {"message": "looks good"}}

    d.capabilities[task.capability].governed_handoff = receiptless_handoff
    result = asyncio.run(d._execute_async(task.id))
    assert result.status == Status.APPROVAL
    assert result.error == "governed_handoff_not_confirmed:missing_canonical_receipt_ids"


def test_proposal_capability_has_explicit_governed_handoff_slot():
    from services.director.models import Capability, Authority, Risk, Sector

    async def handler(params):
        return {"ok": True}

    capability = Capability(
        name="proposal-test", sector=Sector.CONTENT, description="test",
        handler=handler, authority=Authority.SUGGEST, risk=Risk.LOW,
        proposal_capable=True,
    )
    # An ordinary handler is not evidence of canonical governed execution.
    assert capability.governed_handoff is None
    assert capability.handler is handler


def test_native_mission_runtime_routes_are_disabled_by_default(monkeypatch):
    monkeypatch.delenv("KDO_DIRECTOR_MISSION_RUNTIME_ENABLED", raising=False)
    app = _workspace_context_app(monkeypatch, "ws-a", role="viewer")
    response = TestClient(app).get(
        "/missions/mission-a/orchestration/timeline",
        headers={"Authorization": "Bearer valid-user-session"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Director native mission runtime is disabled"


def test_native_mission_timeline_uses_trusted_workspace_and_read_only_access(monkeypatch):
    monkeypatch.setenv("KDO_DIRECTOR_MISSION_RUNTIME_ENABLED", "true")
    app = _workspace_context_app(monkeypatch, "ws-a", role="viewer")

    class FakeRuntime:
        async def load_snapshot(self, *, mission_id, workspace_id):
            return SimpleNamespace(
                version=3, status="running",
                state_snapshot={"task_assignments": {"task-a": {"engine_id": "content_intelligence"}}},
            )

        async def timeline(self, *, mission_id, workspace_id, limit):
            assert mission_id == "mission-a"
            assert workspace_id == "ws-a"
            assert limit == 25
            return [{"event_type": "mission.admitted", "sequence_number": 1}]

    app.state.director_mission_runtime = FakeRuntime()
    response = TestClient(app).get(
        "/missions/mission-a/orchestration/timeline?limit=25",
        headers={"Authorization": "Bearer valid-user-session"},
    )
    assert response.status_code == 200
    assert response.json()["workspace_id"] == "ws-a"
    assert response.json()["events"][0]["event_type"] == "mission.admitted"


def test_native_mission_timeline_rejects_unsigned_species_service_context(monkeypatch):
    monkeypatch.setenv("KDO_DIRECTOR_MISSION_RUNTIME_ENABLED", "true")
    app = _build_director_app(monkeypatch)

    @app.middleware("http")
    async def platform_context_without_workspace(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-a", principal_id="kcpg-service", role="species",
            species_privileges=True,
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    app.state.director_mission_runtime = SimpleNamespace()
    response = TestClient(app).get(
        "/missions/mission-a/orchestration/timeline",
        headers={"Authorization": "Bearer valid-user-session"},
    )
    assert response.status_code == 403


def test_native_mission_runtime_accepts_only_signed_kcpg_human_actor_context(monkeypatch):
    import hashlib
    import hmac
    import time

    secret = "test-kcpg-context-secret-that-is-long-enough"
    monkeypatch.setenv("KCPG_CONTEXT_SIGNING_SECRET", secret)
    monkeypatch.setenv("KDO_DIRECTOR_MISSION_RUNTIME_ENABLED", "true")
    app = _build_director_app(monkeypatch)

    @app.middleware("http")
    async def species_service_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-a", principal_id="kcpg-service", role="species",
            species_privileges=True,
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    class FakeRuntime:
        async def load_snapshot(self, *, mission_id, workspace_id):
            return SimpleNamespace(
                version=1, status="admitted",
                state_snapshot={"task_assignments": {}, "engine_progress": {}, "pending_questions": {}, "budget": {}},
            )

        async def timeline(self, *, mission_id, workspace_id, limit):
            assert workspace_id == "ws-a"
            return [{"event_type": "mission.admitted"}]

    app.state.director_mission_runtime = FakeRuntime()
    timestamp = str(int(time.time() * 1000))
    request_id = "request-kcpg-runtime-001"
    workspace_id = "ws-a"
    actor_id = "human-operator-7"
    actor_role = "operator"
    canonical = "|".join((timestamp, request_id, workspace_id, actor_id, actor_role))
    signature = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()

    response = TestClient(app).get(
        "/missions/mission-a/orchestration/timeline",
        headers={
            "Authorization": "Bearer valid-user-session",
            "X-Workspace-ID": workspace_id,
            "X-Correlation-ID": request_id,
            "X-KCPG-Context-Timestamp": timestamp,
            "X-KCPG-Context-Request-ID": request_id,
            "X-KCPG-Context-Workspace-ID": workspace_id,
            "X-KCPG-Context-Actor-ID": actor_id,
            "X-KCPG-Context-Actor-Role": actor_role,
            "X-KCPG-Context-Signature": signature,
        },
    )
    assert response.status_code == 200
    assert response.json()["workspace_id"] == workspace_id


def test_native_mission_assignment_endpoint_awaits_workspace_binding_and_calls_runtime(monkeypatch):
    from types import SimpleNamespace
    from services.director import Director, create_app

    monkeypatch.setenv("KDO_DIRECTOR_MISSION_RUNTIME_ENABLED", "true")
    d = Director(supabase_client=None)
    app = create_app(d)

    @app.middleware("http")
    async def tenant_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-a", principal_id="trusted-operator", role="owner",
            species_privileges=False,
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    d.tasks["task-a"] = SimpleNamespace(
        id="task-a", workspace_id="ws-a", mission_id="mission-a",
    )

    class FakeRuntime:
        async def assign_task(self, **kwargs):
            assert kwargs["workspace_id"] == "ws-a"
            assert kwargs["task_id"] == "task-a"
            assert kwargs["director_id"] == "trusted-operator"
            return SimpleNamespace(version=7)

    app.state.director_mission_runtime = FakeRuntime()
    response = TestClient(app).post(
        "/missions/mission-a/orchestration/tasks/task-a/assign",
        headers={"Authorization": "Bearer valid-user-session"},
        json={
            "engine_id": "content_intelligence",
            "capability": "research.analyze",
            "idempotency_key": "assign-api-test-001",
            "correlation_id": "correlation-api-test-001",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["mission_version"] == 7


def test_native_mission_execution_endpoint_awaits_workspace_binding_and_returns_contribution(monkeypatch):
    from types import SimpleNamespace
    from services.director import Director, create_app

    monkeypatch.setenv("KDO_DIRECTOR_MISSION_RUNTIME_ENABLED", "true")
    d = Director(supabase_client=None)
    app = create_app(d)

    @app.middleware("http")
    async def tenant_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-a", principal_id="trusted-operator", role="owner",
            species_privileges=False,
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    d.tasks["task-a"] = SimpleNamespace(
        id="task-a", workspace_id="ws-a", mission_id="mission-a",
    )

    class FakeRuntime:
        async def execute_approved_task(self, **kwargs):
            assert kwargs["workspace_id"] == "ws-a"
            assert kwargs["task_id"] == "task-a"
            return SimpleNamespace(
                engine_id="content_intelligence", status="completed",
                summary="Native contribution", confidence=0.9,
                evidence=({"ref": "evidence:one"},), limitations=(),
            )

    app.state.director_mission_runtime = FakeRuntime()
    response = TestClient(app).post(
        "/missions/mission-a/orchestration/tasks/task-a/execute",
        headers={"Authorization": "Bearer valid-user-session"},
        json={
            "engine_id": "content_intelligence",
            "objective": "Run the approved task",
            "inputs": {},
            "idempotency_prefix": "execute-api-test-001",
            "correlation_id": "correlation-api-test-001",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["summary"] == "Native contribution"


def test_native_mission_reconciliation_endpoint_uses_trusted_workspace_operator(monkeypatch):
    from types import SimpleNamespace
    from services.director import Director, create_app

    monkeypatch.setenv("KDO_DIRECTOR_MISSION_RUNTIME_ENABLED", "true")
    director = Director(supabase_client=None)
    app = create_app(director)

    @app.middleware("http")
    async def tenant_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-a", principal_id="trusted-recovery-operator", role="operator",
            species_privileges=False,
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    director.tasks["task-recovery"] = SimpleNamespace(
        id="task-recovery", workspace_id="ws-a", mission_id="mission-a",
    )

    class FakeRuntime:
        async def reconcile_and_record_task_attempt(self, **kwargs):
            assert kwargs == {
                "mission_id": "mission-a",
                "workspace_id": "ws-a",
                "task_id": "task-recovery",
                "correlation_id": "reconcile-correlation-001",
            }
            return SimpleNamespace(
                attempt_id="attempt-recovery-001",
                engine_id="content_intelligence",
                status="unknown",
                authoritative=False,
                evidence_ref=None,
                result={},
            )

    app.state.director_mission_runtime = FakeRuntime()
    response = TestClient(app).post(
        "/missions/mission-a/orchestration/tasks/task-recovery/reconcile",
        headers={"Authorization": "Bearer valid-user-session"},
        json={"correlation_id": "reconcile-correlation-001"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "unknown"
    assert response.json()["authoritative"] is False
    assert response.json()["execution_authority_changed"] is False


def test_unsigned_species_service_cannot_approve_workspace_mission_task(monkeypatch):
    from services.director import Director, create_app
    from services.director.models import Task, Sector, Authority, Risk, Status

    d = Director(supabase_client=None)
    d.tasks["workspace-task"] = Task(
        id="workspace-task", sector=Sector.CONTENT, description="mission task",
        capability="self_diagnostic", authority=Authority.AUTO, risk=Risk.LOW,
        status=Status.APPROVAL, workspace_id="ws-a", mission_id="mission-a",
    )
    app = create_app(d)

    @app.middleware("http")
    async def species_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-a", principal_id="kcpg-service", role="species",
            species_privileges=True,
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    response = TestClient(app).post(
        "/tasks/workspace-task/approve",
        headers={"Authorization": "Bearer valid-user-session"},
        json={},
    )
    assert response.status_code == 403
    assert d.tasks["workspace-task"].approved_by is None


def test_native_mission_engine_message_routes_are_workspace_scoped_and_use_assigned_actor(monkeypatch):
    from types import SimpleNamespace
    from services.director import Director, create_app
    from services.director.models import Task, Sector, Authority, Risk, Status

    monkeypatch.setenv("KDO_DIRECTOR_MISSION_RUNTIME_ENABLED", "true")
    d = Director(supabase_client=None)
    d.tasks["task-a"] = Task(
        id="task-a", sector=Sector.CONTENT, description="mission task",
        capability="self_diagnostic", authority=Authority.AUTO, risk=Risk.LOW,
        status=Status.APPROVAL, workspace_id="ws-a", mission_id="mission-a",
    )
    app = create_app(d)

    @app.middleware("http")
    async def tenant_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-a", principal_id="trusted-operator", role="owner",
            species_privileges=False,
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    class FakeRuntime:
        async def load_snapshot(self, *, mission_id, workspace_id):
            assert mission_id == "mission-a"
            assert workspace_id == "ws-a"
            return SimpleNamespace(
                version=4,
                state_snapshot={
                    "task_assignments": {"task-a": {"engine_id": "content_intelligence"}},
                    "pending_questions": {
                        "question-123": {
                            "task_id": "task-a",
                            "from_engine": "content_intelligence",
                            "to_engine": "meta_engine",
                        }
                    },
                },
            )

        async def ask_engine(self, **kwargs):
            assert kwargs["actor_engine_id"] == "content_intelligence"
            assert kwargs["recipient_engine_id"] == "meta_engine"
            return SimpleNamespace(version=5)

        async def answer_engine_question(self, **kwargs):
            assert kwargs["question_message_id"] == "question-123"
            return SimpleNamespace(
                engine_id="meta_engine", status="completed", summary="Independent review",
                confidence=0.88, evidence=({"ref": "review:evidence"},), limitations=(),
            )

    app.state.director_mission_runtime = FakeRuntime()
    client = TestClient(app)
    headers = {"Authorization": "Bearer valid-user-session"}
    asked = client.post(
        "/missions/mission-a/orchestration/messages",
        headers=headers,
        json={
            "task_id": "task-a", "recipient_engine_id": "meta_engine",
            "question": "Challenge the source evidence.",
            "recipient_capability": "review.analyze",
            "message_id": "question-123", "idempotency_key": "question-idempotency-123",
            "correlation_id": "correlation-message-123", "round_number": 1,
        },
    )
    assert asked.status_code == 200
    assert asked.json()["from_engine"] == "content_intelligence"
    assert asked.json()["status"] == "pending_response"

    answered = client.post(
        "/missions/mission-a/orchestration/messages/question-123/answer",
        headers=headers,
        json={
            "answer_message_id": "answer-message-123",
            "idempotency_prefix": "answer-idempotency-123",
            "correlation_id": "correlation-message-123",
        },
    )
    assert answered.status_code == 200
    assert answered.json()["engine_id"] == "meta_engine"
    assert answered.json()["summary"] == "Independent review"


def test_native_mission_task_rejection_is_journalled_through_runtime(monkeypatch):
    from types import SimpleNamespace
    from services.director import Director, create_app
    from services.director.models import Task, Sector, Authority, Risk, Status

    monkeypatch.setenv("KDO_DIRECTOR_MISSION_RUNTIME_ENABLED", "true")
    d = Director(supabase_client=None)
    d.tasks["task-cancel"] = Task(
        id="task-cancel", sector=Sector.CONTENT, description="mission task",
        capability="self_diagnostic", authority=Authority.AUTO, risk=Risk.LOW,
        status=Status.APPROVAL, workspace_id="ws-a", mission_id="mission-a",
    )
    app = create_app(d)

    @app.middleware("http")
    async def tenant_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-a", principal_id="trusted-operator", role="owner",
            species_privileges=False,
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    class FakeStore:
        async def get_workspace_task(self, **kwargs):
            assert kwargs["task_id"] == "task-cancel"
            assert kwargs["workspace_id"] == "ws-a"
            assert kwargs["mission_id"] == "mission-a"
            return {
                "status": "cancelled",
                "payload": {"status": "cancelled", "error": "Rejected by trusted-operator: Evidence was insufficient"},
            }

    class FakeRuntime:
        store = FakeStore()

        async def cancel_task(self, **kwargs):
            assert kwargs["mission_id"] == "mission-a"
            assert kwargs["workspace_id"] == "ws-a"
            assert kwargs["task_id"] == "task-cancel"
            assert kwargs["operator_id"] == "trusted-operator"
            assert kwargs["reason"].startswith("Rejected by trusted-operator:")
            assert kwargs["idempotency_key"].startswith("director-task-cancel:")
            return SimpleNamespace(version=8)

    app.state.director_mission_runtime = FakeRuntime()
    response = TestClient(app).post(
        "/tasks/task-cancel/reject",
        headers={"Authorization": "Bearer valid-user-session"},
        json={"reason": "Evidence was insufficient"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    assert response.json()["error"] == "Rejected by trusted-operator: Evidence was insufficient"
    assert d.tasks["task-cancel"].status == Status.CANCELLED


def test_already_approved_retry_rechecks_canonical_persistence_before_journaling(monkeypatch):
    import asyncio
    from datetime import datetime, timezone
    from services.director import Director, create_app
    from services.director.models import Authority, Risk, Sector, Status, Task

    monkeypatch.setenv("KDO_DIRECTOR_MISSION_RUNTIME_ENABLED", "true")
    d = Director(supabase_client=None)
    task = Task(
        id="approval-retry-task",
        sector=Sector.CONTENT,
        description="approval retry",
        capability="self_diagnostic",
        authority=Authority.SUGGEST,
        risk=Risk.LOW,
        status=Status.APPROVED,
        approved_at=datetime.now(timezone.utc),
        approved_by="trusted-user-123",
        workspace_id="ws-a",
        mission_id="mission-a",
    )
    d.tasks[task.id] = task

    saves = []
    async def save_task(candidate):
        saves.append((candidate.status, candidate.approved_by, candidate.approved_at))
        await asyncio.sleep(0)
        return True
    d.store.save_task = save_task

    class Runtime:
        def __init__(self):
            self.calls = []
        async def approve_task(self, **kwargs):
            self.calls.append(kwargs)
            return None

    runtime = Runtime()
    app = create_app(d)
    app.state.director_mission_runtime = runtime

    @app.middleware("http")
    async def tenant_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-a", principal_id="trusted-user-123",
            role="owner", species_privileges=False,
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    response = TestClient(app).post(
        f"/tasks/{task.id}/approve",
        headers={"Authorization": "Bearer valid-user-session"},
        json={"actor": "untrusted-body-actor"},
    )
    assert response.status_code == 200
    assert len(saves) == 1
    assert saves[0][0] == Status.APPROVED
    assert saves[0][1] == "trusted-user-123"
    assert len(runtime.calls) == 1


def test_already_approved_retry_does_not_journal_when_canonical_save_is_unacknowledged(monkeypatch):
    from datetime import datetime, timezone
    from services.director import Director, create_app
    from services.director.models import Authority, Risk, Sector, Status, Task

    monkeypatch.setenv("KDO_DIRECTOR_MISSION_RUNTIME_ENABLED", "true")
    d = Director(supabase_client=None)
    task = Task(
        id="approval-retry-unacknowledged",
        sector=Sector.CONTENT,
        description="approval retry",
        capability="self_diagnostic",
        authority=Authority.SUGGEST,
        risk=Risk.LOW,
        status=Status.APPROVED,
        approved_at=datetime.now(timezone.utc),
        approved_by="trusted-user-123",
        workspace_id="ws-a",
        mission_id="mission-a",
    )
    d.tasks[task.id] = task

    saves = []
    async def save_task(candidate):
        saves.append(candidate.id)
        return False
    d.store.save_task = save_task

    class Runtime:
        calls = 0
        async def approve_task(self, **kwargs):
            self.calls += 1
            return None

    runtime = Runtime()
    app = create_app(d)
    app.state.director_mission_runtime = runtime

    @app.middleware("http")
    async def tenant_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-a", principal_id="trusted-user-123",
            role="owner", species_privileges=False,
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    response = TestClient(app).post(
        f"/tasks/{task.id}/approve",
        headers={"Authorization": "Bearer valid-user-session"},
        json={"actor": "untrusted-body-actor"},
    )
    assert response.status_code == 503
    assert saves == [task.id]
    assert runtime.calls == 0
    assert task.status == Status.APPROVED


def test_concurrent_same_actor_approval_retries_each_acknowledge_canonical_store(monkeypatch):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timezone
    from services.director import Director, create_app
    from services.director.models import Authority, Risk, Sector, Status, Task

    monkeypatch.setenv("KDO_DIRECTOR_MISSION_RUNTIME_ENABLED", "true")
    d = Director(supabase_client=None)
    task = Task(
        id="approval-concurrent-retry",
        sector=Sector.CONTENT,
        description="concurrent approval retry",
        capability="self_diagnostic",
        authority=Authority.SUGGEST,
        risk=Risk.LOW,
        status=Status.APPROVED,
        approved_at=datetime.now(timezone.utc),
        approved_by="trusted-user-123",
        workspace_id="ws-a",
        mission_id="mission-a",
    )
    d.tasks[task.id] = task

    saves = []
    async def save_task(candidate):
        saves.append(candidate.id)
        await asyncio.sleep(0.03)
        return True
    d.store.save_task = save_task

    class Runtime:
        def __init__(self):
            self.calls = []
        async def approve_task(self, **kwargs):
            self.calls.append(kwargs)
            return None

    runtime = Runtime()
    app = create_app(d)
    app.state.director_mission_runtime = runtime

    @app.middleware("http")
    async def tenant_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-a", principal_id="trusted-user-123",
            role="owner", species_privileges=False,
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    def submit_approval():
        with TestClient(app) as client:
            return client.post(
                f"/tasks/{task.id}/approve",
                headers={"Authorization": "Bearer valid-user-session"},
                json={"actor": "untrusted-body-actor"},
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: submit_approval(), range(2)))

    assert [response.status_code for response in responses] == [200, 200]
    assert saves == [task.id, task.id]
    assert len(runtime.calls) == 2



def test_legacy_non_mission_task_approval_keeps_existing_persistence_path(monkeypatch):
    from services.director import Director, create_app
    from services.director.models import Authority, Risk, Sector, Status, Task

    monkeypatch.delenv("KDO_DIRECTOR_MISSION_RUNTIME_ENABLED", raising=False)
    director = Director(supabase_client=None)
    task = Task(
        id="legacy-non-mission-approval",
        sector=Sector.CONTENT,
        description="legacy task approval compatibility",
        capability="self_diagnostic",
        authority=Authority.SUGGEST,
        risk=Risk.LOW,
        status=Status.APPROVAL,
        workspace_id="ws-legacy",
    )
    director.tasks[task.id] = task
    persisted = []

    async def save_task(candidate):
        persisted.append({
            "id": candidate.id,
            "status": candidate.status,
            "approved_by": candidate.approved_by,
            "approved_at": candidate.approved_at,
        })
        return True

    director.store.save_task = save_task
    app = create_app(director)

    @app.middleware("http")
    async def trusted_workspace_operator(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-legacy",
            principal_id="trusted-legacy-operator",
            role="operator",
            species_privileges=False,
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    response = TestClient(app).post(
        f"/tasks/{task.id}/approve",
        headers={"Authorization": "Bearer valid-user-session"},
        json={"actor": "attacker-controlled-body-actor"},
    )

    assert response.status_code == 200, response.text
    assert len(persisted) == 1
    assert persisted[0]["id"] == task.id
    assert persisted[0]["status"] == Status.APPROVED
    assert persisted[0]["approved_by"] == "trusted-legacy-operator"
    assert persisted[0]["approved_at"] is not None
    assert response.json()["approved_by"] == "trusted-legacy-operator"
    assert task.status == Status.APPROVED
    assert task.approved_by == "trusted-legacy-operator"
    assert task.approved_at is not None
