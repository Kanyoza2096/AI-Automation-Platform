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
    result = asyncio.run(d._execute_async(task.id, workspace_id="ws-a"))

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

    result = asyncio.run(d._execute_async(task.id, workspace_id="ws-a"))
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
    result = asyncio.run(d._execute_async(task.id, workspace_id="ws-a"))
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
    result = asyncio.run(d._execute_async(task.id, workspace_id="ws-a"))
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


def test_workspace_owner_cannot_read_platform_engineering_metadata(monkeypatch):
    app = _workspace_context_app(monkeypatch, "ws-a", role="owner")
    client = TestClient(app)
    headers = {"Authorization": "Bearer valid-user-session"}

    awareness = client.get("/engineering-awareness", headers=headers)
    ledger = client.get("/engineering-awareness/ledger", headers=headers)

    assert awareness.status_code == 403
    assert ledger.status_code == 403


def test_director_super_admin_can_list_signals_across_workspaces(monkeypatch):
    from services.director import Director, create_app
    from services.director.models import DirectorSignal, Sector

    d = Director(supabase_client=None)
    d.signals["signal-a"] = DirectorSignal(
        id="signal-a", detector="test", sector=Sector.CONTENT,
        signal_type="test", subject_type="workspace", subject_id="a",
        workspace_id="ws-a", confidence=0.9, rationale="test", payload={},
    )
    d.signals["signal-b"] = DirectorSignal(
        id="signal-b", detector="test", sector=Sector.CONTENT,
        signal_type="test", subject_type="workspace", subject_id="b",
        workspace_id="ws-b", confidence=0.9, rationale="test", payload={},
    )
    app = create_app(d)

    @app.middleware("http")
    async def platform_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-a", role="owner", platform_role="super_admin",
            species_privileges=False,
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    response = TestClient(app).get(
        "/signals", headers={"Authorization": "Bearer valid-user-session"}
    )
    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert {item["id"] for item in response.json()["signals"]} == {"signal-a", "signal-b"}


def test_director_workspace_signal_list_remains_isolated(monkeypatch):
    from services.director import Director, create_app
    from services.director.models import DirectorSignal, Sector

    d = Director(supabase_client=None)
    for signal_id, workspace_id in (("signal-a", "ws-a"), ("signal-b", "ws-b")):
        d.signals[signal_id] = DirectorSignal(
            id=signal_id, detector="test", sector=Sector.CONTENT,
            signal_type="test", subject_type="workspace", subject_id=signal_id,
            workspace_id=workspace_id, confidence=0.9, rationale="test", payload={},
        )
    app = create_app(d)

    @app.middleware("http")
    async def tenant_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-a", role="owner", species_privileges=False,
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    response = TestClient(app).get(
        "/signals", headers={"Authorization": "Bearer valid-user-session"}
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert [item["id"] for item in response.json()["signals"]] == ["signal-a"]
