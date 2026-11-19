import pytest

from services.director import Director
from services.director.models import Authority, Risk, Sector, Status, Task


def _task(task_id, status=Status.PLANNED):
    return Task(
        id=task_id, sector=Sector.SELF, description=task_id,
        capability="self_diagnostic", authority=Authority.SUGGEST,
        risk=Risk.LOW, status=status,
    )


@pytest.mark.asyncio
async def test_mission_dependency_is_persisted_and_graph_exposes_edge():
    director = Director(supabase_client=None)
    director.tasks = {"a": _task("a"), "b": _task("b")}

    result = await director.add_task_dependency("b", "a")
    graph = director.mission_graph()

    assert result["changed"] is True
    assert director.tasks["b"].depends_on == ["a"]
    assert director.tasks["a"].mission_id == director.tasks["b"].mission_id
    assert graph["edges"] == [{"prerequisite": "a", "dependent": "b", "resolved": True}]
    assert await director.store.get_task("b") is not None


@pytest.mark.asyncio
async def test_workspace_ownership_survives_task_persistence_and_restore():
    director = Director(supabase_client=None)
    from dataclasses import asdict

    class MemoryTaskStore:
        def __init__(self):
            self.rows = []

        async def save_task(self, task):
            self.rows = [row for row in self.rows if row["id"] != task.id]
            self.rows.append({
                "id": task.id,
                "scope_type": task.scope_type,
                "workspace_id": task.workspace_id,
                "payload": asdict(task),
            })
            return True

        async def list_tasks(self, limit=200, **kwargs):
            return list(self.rows)

    director.store = MemoryTaskStore()
    task = Task(
        id="workspace-task", sector=Sector.SELF, description="workspace-task",
        capability="self_diagnostic", authority=Authority.SUGGEST, risk=Risk.LOW,
        workspace_id="workspace-alpha", mission_id="mission-alpha",
    )
    await director.store.save_task(task)

    restarted = Director(supabase_client=None)
    restarted.store = director.store
    result = await restarted._restore_tasks()

    assert result["restored"] == 1
    restored = restarted.tasks["workspace-task"]
    assert restored.workspace_id == "workspace-alpha"
    assert restored.mission_id == "mission-alpha"


@pytest.mark.asyncio
async def test_mission_dependency_rejects_cycles_and_self_edges():
    director = Director(supabase_client=None)
    director.tasks = {"a": _task("a"), "b": _task("b"), "c": _task("c")}
    await director.add_task_dependency("b", "a")
    await director.add_task_dependency("c", "b")

    with pytest.raises(ValueError, match="cycle"):
        await director.add_task_dependency("a", "c")
    with pytest.raises(ValueError, match="itself"):
        await director.add_task_dependency("a", "a")


@pytest.mark.asyncio
async def test_scheduler_does_not_run_dependent_before_prerequisite_succeeds(monkeypatch):
    director = Director(supabase_client=None)
    prerequisite = _task("a")
    dependent = _task("b")
    dependent.depends_on = ["a"]
    director.tasks = {"a": prerequisite, "b": dependent}
    executed = []

    async def record(task_id):
        executed.append(task_id)

    monkeypatch.setattr(director, "_execute_with_semaphore", record)
    await director._execute_due_async()
    assert executed == ["a"]

    prerequisite.status = Status.COMPLETED
    await director._execute_due_async()
    assert executed == ["a", "b"]


@pytest.mark.asyncio
async def test_scheduler_blocks_dependent_when_prerequisite_fails(monkeypatch):
    director = Director(supabase_client=None)
    prerequisite = _task("a", Status.FAILED)
    dependent = _task("b")
    dependent.depends_on = ["a"]
    director.tasks = {"a": prerequisite, "b": dependent}
    executed = []

    async def record(task_id):
        executed.append(task_id)

    monkeypatch.setattr(director, "_execute_with_semaphore", record)
    await director._execute_due_async()

    assert executed == []
    assert dependent.status == Status.BLOCKED
    assert dependent.error == "mission_dependency_unavailable:a"


@pytest.mark.asyncio
async def test_mission_dependency_rejects_cross_mission_edge():
    director = Director(supabase_client=None)
    a, b = _task("a"), _task("b")
    a.mission_id, b.mission_id = "mission-a", "mission-b"
    director.tasks = {"a": a, "b": b}

    with pytest.raises(ValueError, match="different missions"):
        await director.add_task_dependency("b", "a")


@pytest.mark.asyncio
async def test_startup_restore_rebuilds_persisted_mission_graph():
    director = Director(supabase_client=None)
    prerequisite = _task("persisted-a", Status.COMPLETED)
    dependent = _task("persisted-b")
    dependent.depends_on = ["persisted-a"]
    prerequisite.mission_id = dependent.mission_id = "mission-persisted"
    await director.store.save_task(prerequisite)
    await director.store.save_task(dependent)

    restarted = Director(supabase_client=None)
    restarted.store = director.store
    result = await restarted._restore_tasks()

    assert result == {"restored": 2, "flagged_for_reconciliation": 0, "errors": 0}
    graph = restarted.mission_graph("mission-persisted")
    assert {node["task_id"] for node in graph["nodes"]} == {"persisted-a", "persisted-b"}
    assert graph["edges"] == [{"prerequisite": "persisted-a", "dependent": "persisted-b", "resolved": True}]


@pytest.mark.asyncio
async def test_restore_flags_in_flight_task_to_prevent_duplicate_side_effect():
    director = Director(supabase_client=None)
    in_flight = _task("ambiguous", Status.EXECUTING)
    await director.store.save_task(in_flight)

    restarted = Director(supabase_client=None)
    restarted.store = director.store
    result = await restarted._restore_tasks()

    restored = restarted.tasks["ambiguous"]
    assert result["flagged_for_reconciliation"] == 1
    assert restored.status == Status.FLAGGED
    assert restored.result["status"] == "reconciliation_required"
    persisted = await restarted.store.get_task("ambiguous")
    assert persisted["status"] == Status.FLAGGED.value


class _MissionAdmissionStore:
    def __init__(self):
        self.receipts = {}
        self.brand_checks = []

    async def stage_mission_admission(self, **kwargs):
        key = (kwargs["workspace_id"], kwargs["idempotency_key"])
        prior = self.receipts.get(key)
        if prior:
            if prior["fingerprint"] != kwargs["request_fingerprint"]:
                raise ValueError("mission_idempotency_conflict")
            return {"mission_id": prior["mission_id"], "task_ids": prior["task_ids"], "replayed": True}
        ids = [row["id"] for row in kwargs["tasks"]]
        self.receipts[key] = {
            "fingerprint": kwargs["request_fingerprint"],
            "mission_id": kwargs["mission_id"],
            "task_ids": ids,
        }
        return {"mission_id": kwargs["mission_id"], "task_ids": ids, "replayed": False}

    async def brand_belongs_to_workspace(self, brand_id, workspace_id):
        self.brand_checks.append((brand_id, workspace_id))
        return brand_id == "brand-owned" and workspace_id == "workspace-a"



def _bind_stageable_health_capability(director, monkeypatch):
    from types import SimpleNamespace

    capability = SimpleNamespace(
        name="test_brand_health",
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
    director.capabilities = {"test_brand_health": capability}
    director.capability_binding_report["test_brand_health"] = "bound"
    monkeypatch.setattr(director, "capability_catalog", lambda: {
        "test_brand_health": {
            "mission_staging_ready": True,
            "runtime_binding_confirmed": True,
            "binding_status": "bound",
        }
    })


def _stageable_health_plan():
    return {
        "plan_id": "plan-health-1",
        "objective": {"source_collaboration_run_id": "collab-run-123"},
        "source_context_fingerprint": "context-fingerprint-1",
        "source_context_generated_at": "2026-11-16T08:00:00+00:00",
        "steps": [{
            "step_id": "health",
            "capability": "test_brand_health",
            "sector": "brands",
            "objective": "Assess the selected workspace brand",
            "target": "Workspace-owned brand health",
            "rationale": "Collect current evidence",
            "depends_on": [],
            "acceptance_criteria": ["Every returned service has a reported status."],
        }],
    }


@pytest.mark.asyncio
async def test_strategic_mission_admission_stages_workspace_tasks_without_execution(monkeypatch):
    director = Director(supabase_client=None)
    _bind_stageable_health_capability(director, monkeypatch)
    director.store = _MissionAdmissionStore()
    async def load_plan(plan_id):
        return _stageable_health_plan()
    monkeypatch.setattr(director, "load_strategic_plan", load_plan)
    async def verified(plan, max_age_seconds=300):
        return {"verdict": "verified_for_governance_review"}
    monkeypatch.setattr(director, "verify_strategic_plan", verified)

    result = await director.stage_strategic_mission(
        plan_id="plan-health-1", workspace_id="workspace-a",
        idempotency_key="operator-request-001", step_parameters={"health": {"brand_id": "brand-owned"}}, actor="operator-1",
    )

    task = director.tasks[result["task_ids"][0]]
    assert result["status"] == "staged_for_approval"
    assert result["authority"] == "none"
    assert result["execution_permitted"] is False
    assert result["approval_required"] is True
    assert task.workspace_id == "workspace-a"
    assert task.mission_id == result["mission_id"]
    assert task.status == Status.APPROVAL
    assert task.params == {"brand_id": "brand-owned"}
    assert "operator:operator-1" in task.sources
    assert "collaboration_run:collab-run-123" in task.sources
    assert result["source_collaboration_run_id"] == "collab-run-123"

    replay = await director.stage_strategic_mission(
        plan_id="plan-health-1", workspace_id="workspace-a",
        idempotency_key="operator-request-001", step_parameters={"health": {"brand_id": "brand-owned"}}, actor="operator-1",
    )
    assert replay["replayed"] is True
    assert replay["task_ids"] == result["task_ids"]


@pytest.mark.asyncio
async def test_strategic_mission_admission_rejects_unlisted_params_and_unowned_brand(monkeypatch):
    director = Director(supabase_client=None)
    _bind_stageable_health_capability(director, monkeypatch)
    director.store = _MissionAdmissionStore()
    plan = _stageable_health_plan()
    async def load_plan(plan_id):
        return plan
    monkeypatch.setattr(director, "load_strategic_plan", load_plan)
    async def verified(plan, max_age_seconds=300):
        return {"verdict": "verified_for_governance_review"}
    monkeypatch.setattr(director, "verify_strategic_plan", verified)

    with pytest.raises(ValueError, match="invalid_step_parameters"):
        await director.stage_strategic_mission(
            plan_id="plan-health-1", workspace_id="workspace-a",
            idempotency_key="operator-request-002", step_parameters={"health": {"brand_id": "brand-owned", "extra": 1}}, actor="operator-1",
        )
    with pytest.raises(ValueError, match="brand_not_found_in_workspace"):
        await director.stage_strategic_mission(
            plan_id="plan-health-1", workspace_id="workspace-a",
            idempotency_key="operator-request-003", step_parameters={"health": {"brand_id": "other-brand"}}, actor="operator-1",
        )

def test_global_diagnostic_capabilities_cannot_be_staged_as_workspace_missions():
    director = Director(supabase_client=None)
    def bound_handler(params):
        return {"ok": True}
    for name in ("run_health_scan", "run_guardian_scan", "self_diagnostic"):
        director.bind_capability(name, bound_handler)
        director.capability_binding_report[name] = "bound"
    catalog = director.capability_catalog()
    for name in ("run_health_scan", "run_guardian_scan", "self_diagnostic"):
        assert catalog[name]["runtime_binding_confirmed"] is True
        assert catalog[name]["workspace_target_bound"] is False
        assert catalog[name]["mission_staging_ready"] is False



@pytest.mark.asyncio
async def test_strategic_mission_admission_can_use_v2_runtime_atomically(monkeypatch):
    from services.director.orchestration import store as runtime_store_module

    director = Director(supabase_client=None)
    _bind_stageable_health_capability(director, monkeypatch)

    class LegacyStoreMustNotBeUsed(_MissionAdmissionStore):
        async def stage_mission_admission(self, **kwargs):
            raise AssertionError("v2 admission must not call the legacy RPC")

    class FakeMissionRuntimeStore:
        def __init__(self, director_store):
            self.director_store = director_store
            self.admission = None

        async def admit_workspace_mission(self, **kwargs):
            self.admission = kwargs
            return {
                "mission_id": kwargs["mission_id"],
                "task_ids": [row["id"] for row in kwargs["tasks"]],
                "replayed": False,
                "mission_version": 1,
                "mission_status": "admitted",
            }

    runtime_instances = []

    def runtime_factory(director_store):
        instance = FakeMissionRuntimeStore(director_store)
        runtime_instances.append(instance)
        return instance

    monkeypatch.setattr(runtime_store_module, "MissionRuntimeStore", runtime_factory)
    monkeypatch.setenv("KDO_DIRECTOR_MISSION_RUNTIME_V2", "true")
    director.store = LegacyStoreMustNotBeUsed()
    async def load_plan(plan_id):
        return _stageable_health_plan()
    monkeypatch.setattr(director, "load_strategic_plan", load_plan)

    async def verified(plan, max_age_seconds=300):
        return {"verdict": "verified_for_governance_review"}

    monkeypatch.setattr(director, "verify_strategic_plan", verified)

    result = await director.stage_strategic_mission(
        plan_id="plan-health-1",
        workspace_id="workspace-a",
        idempotency_key="operator-request-v2",
        step_parameters={"health": {"brand_id": "brand-owned"}},
        actor="operator-1",
    )

    assert result["durable_runtime"] == "v2"
    assert result["approval_required"] is True
    assert result["execution_permitted"] is False
    assert runtime_instances[0].admission["workspace_id"] == "workspace-a"
    assert runtime_instances[0].admission["tasks"][0]["payload"]["scope_type"] == "workspace"
    assert "collaboration_run:collab-run-123" in runtime_instances[0].admission["tasks"][0]["payload"]["sources"]
    assert result["source_collaboration_run_id"] == "collab-run-123"
