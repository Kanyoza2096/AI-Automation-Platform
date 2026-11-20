"""HTTP-level regression harness for governed Director v2 mission admission.

The runtime store is replaced with an in-memory fake: this exercises the real
FastAPI route and Director staging method without writing to Supabase, running
tasks, or dispatching an engine.
"""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.director import Director, create_app
from services.director.models import Authority, Risk, Sector, Status


def _stageable_plan():
    return {
        "plan_id": "plan-route-harness",
        "objective": {"objective_id": "objective-route-harness"},
        "source_context_fingerprint": "route-harness-context",
        "source_context_generated_at": "2026-11-20T00:00:00+00:00",
        "steps": [{
            "step_id": "review",
            "capability": "route_harness_review",
            "sector": "brands",
            "objective": "Review workspace-owned evidence without changing it",
            "target": "Workspace-owned evidence",
            "rationale": "Read-only review",
            "depends_on": [],
            "acceptance_criteria": ["The result is evidence-linked."],
        }],
    }


class _FakeDirectorStore:
    async def brand_belongs_to_workspace(self, brand_id, workspace_id):
        return brand_id == "brand-route-harness" and workspace_id == "workspace-route-harness"


class _FakeMissionRuntimeStore:
    instances = []

    def __init__(self, director_store):
        self.director_store = director_store
        self.admissions = []
        self.__class__.instances.append(self)

    async def admit_workspace_mission(self, **kwargs):
        self.admissions.append(kwargs)
        return {
            "mission_id": kwargs["mission_id"],
            "task_ids": [row["id"] for row in kwargs["tasks"]],
            "replayed": False,
            "mission_version": 1,
            "mission_status": "admitted",
        }


def _build_http_app(monkeypatch):
    from services.director.orchestration import store as runtime_store_module

    monkeypatch.setenv("KDO_DIRECTOR_MISSION_RUNTIME_V2", "true")
    _FakeMissionRuntimeStore.instances = []
    monkeypatch.setattr(
        runtime_store_module, "MissionRuntimeStore", _FakeMissionRuntimeStore
    )

    director = Director(supabase_client=None)
    director.store = _FakeDirectorStore()
    capability = SimpleNamespace(
        name="route_harness_review",
        sector=Sector.BRANDS,
        authority=Authority.SUGGEST,
        risk=Risk.LOW,
        parameter_schema={
            "type": "object",
            "properties": {"brand_id": {"type": "string"}},
            "required": ["brand_id"],
            "additionalProperties": False,
        },
        target_param="brand_id",
        proposal_capable=False,
        mission_staging_mode="read_only_review",
    )
    director.capabilities = {capability.name: capability}
    director.capability_binding_report[capability.name] = "bound"
    monkeypatch.setattr(director, "capability_catalog", lambda: {
        capability.name: {
            "mission_staging_ready": True,
            "runtime_binding_confirmed": True,
            "binding_status": "bound",
        }
    })

    async def load_plan(plan_id):
        return _stageable_plan() if plan_id == "plan-route-harness" else None

    async def verify_plan(plan, max_age_seconds=300):
        return {"verdict": "verified_for_governance_review"}

    monkeypatch.setattr(director, "load_strategic_plan", load_plan)
    monkeypatch.setattr(director, "verify_strategic_plan", verify_plan)

    outer = FastAPI()
    outer.mount("/director", create_app(director))

    @outer.middleware("http")
    async def inject_test_workspace_identity(request, call_next):
        if request.headers.get("X-Harness-Identity") == "trusted":
            request.state.request_context = SimpleNamespace(
                workspace_id="workspace-route-harness",
                role="owner",
                species_privileges=False,
                principal_id="route-harness-operator",
            )
            request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    return outer, director


def test_director_v2_http_admission_uses_trusted_workspace_and_stays_governed(monkeypatch):
    app, director = _build_http_app(monkeypatch)
    client = TestClient(app)

    response = client.post(
        "/director/strategic-plans/plan-route-harness/stage",
        headers={"X-Harness-Identity": "trusted"},
        json={
            "idempotency_key": "route-harness-admission-001",
            "step_parameters": {
                "review": {"brand_id": "brand-route-harness"}
            },
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["durable_runtime"] == "v2"
    assert payload["status"] == "staged_for_approval"
    assert payload["workspace_id"] == "workspace-route-harness"
    assert payload["authority"] == "none"
    assert payload["approval_required"] is True
    assert payload["governance_required"] is True
    assert payload["execution_permitted"] is False

    assert len(_FakeMissionRuntimeStore.instances) == 1
    admission = _FakeMissionRuntimeStore.instances[0].admissions[0]
    assert admission["workspace_id"] == "workspace-route-harness"
    assert admission["plan_id"] == "plan-route-harness"
    assert len(admission["tasks"]) == 1

    task = director.tasks[payload["task_ids"][0]]
    assert task.workspace_id == "workspace-route-harness"
    assert task.status == Status.APPROVAL
    assert task.params == {"brand_id": "brand-route-harness"}
    assert task.sources[-1] == "operator:route-harness-operator"


def test_director_v2_http_admission_rejects_missing_trusted_identity(monkeypatch):
    app, _director = _build_http_app(monkeypatch)
    client = TestClient(app)

    response = client.post(
        "/director/strategic-plans/plan-route-harness/stage",
        json={
            "idempotency_key": "route-harness-no-auth-001",
            "step_parameters": {
                "review": {"brand_id": "brand-route-harness"}
            },
        },
    )

    # With no configured auth credential, Director deliberately fails closed
    # as unavailable (503) rather than pretending authentication is active.
    assert response.status_code == 503
    assert response.json()["detail"] == "Director authentication service is not configured"
    assert _FakeMissionRuntimeStore.instances == []
