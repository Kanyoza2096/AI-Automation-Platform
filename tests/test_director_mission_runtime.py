import copy
from datetime import datetime, timezone
import pytest

from services.director.orchestration import (
    AdapterRegistry,
    EngineContribution,
    MissionOrchestrationRuntime,
    MissionRuntimeStore,
    MissionRuntimeStoreError,
    MissionRuntimeNotFound,
    MissionStateMachine,
    MissionTaskContext,
    MissionTaskNotReady,
    build_default_adapter_registry,
    ConditionEngineMissionAdapter,
)


class MemoryMissionStore:
    def __init__(self):
        self.workspace_id = "workspace-runtime-test"
        self.mission_id = "mission-runtime-test"
        self.task_id = "task-runtime-test"
        self.row = {
            "mission_id": self.mission_id,
            "scope_type": "workspace",
            "workspace_id": self.workspace_id,
            "version": 0,
            "status": "admitted",
            "phase": "admission",
            "task_ids": [self.task_id],
            "state_snapshot": MissionStateMachine.initial_state([self.task_id]),
        }
        self.task = {
            "id": self.task_id,
            "scope_type": "workspace",
            "workspace_id": self.workspace_id,
            "status": "approval_required",
            "payload": {
                "id": self.task_id,
                "scope_type": "workspace",
                "workspace_id": self.workspace_id,
                "mission_id": self.mission_id,
                "status": "approval_required",
            },
        }
        self.events = []
        self.idempotency = {}

    async def get_mission(self, *, mission_id, scope_type, workspace_id=None):
        if (
            mission_id != self.mission_id
            or scope_type != "workspace"
            or workspace_id != self.workspace_id
        ):
            return None
        return copy.deepcopy(self.row)

    async def get_workspace_task(self, *, task_id, workspace_id, mission_id):
        if (
            task_id != self.task_id
            or workspace_id != self.workspace_id
            or mission_id != self.mission_id
        ):
            return None
        return copy.deepcopy(self.task)

    async def sync_workspace_task_status(
        self, *, task_id, workspace_id, mission_id, target_status,
        expected_statuses, result=None, error=None
    ):
        row = await self.get_workspace_task(
            task_id=task_id, workspace_id=workspace_id, mission_id=mission_id
        )
        if row is None:
            raise RuntimeError("task_scope_not_found")
        if row["status"] == target_status:
            return row
        if row["status"] not in set(expected_statuses):
            raise RuntimeError("task_status_conflict")
        self.task["status"] = target_status
        self.task["payload"]["status"] = target_status
        if result is not None:
            self.task["payload"]["result"] = copy.deepcopy(dict(result))
        if error is not None:
            self.task["payload"]["error"] = error
        elif target_status in {"approved", "executing", "completed", "verified", "approval_required"}:
            self.task["payload"]["error"] = None
        return copy.deepcopy(self.task)

    async def append_event(self, event):
        existing = self.idempotency.get(event.idempotency_key)
        if existing:
            if existing["fingerprint"] != event.request_fingerprint:
                return {"error": "director_mission_event_idempotency_conflict"}
            return {"replayed": True, "version": self.row["version"]}
        if event.expected_version != self.row["version"]:
            return {"error": "director_mission_version_conflict"}
        # Mirror the production RPC transaction: validate and update the
        # canonical task before committing the mission snapshot/event.
        if event.task_sync:
            sync = dict(event.task_sync)
            target = sync["target_status"]
            current = self.task["status"]
            if current != target and current not in set(sync["expected_statuses"]):
                return {"error": "atomic_task_sync_status_conflict"}
            if current != target:
                next_payload = copy.deepcopy(self.task["payload"])
                next_payload["status"] = target
                if sync.get("result") is not None:
                    next_payload["result"] = copy.deepcopy(sync["result"])
                if sync.get("error") is not None:
                    next_payload["error"] = sync["error"]
                elif target in {"approved", "executing", "completed", "verified", "approval_required"}:
                    next_payload["error"] = None
                self.task["status"] = target
                self.task["payload"] = next_payload
        version = self.row["version"] + 1
        self.row.update({
            "version": version,
            "status": event.status,
            "phase": event.phase,
            "state_snapshot": copy.deepcopy(dict(event.state_snapshot)),
        })
        self.events.append({
            "sequence_number": version,
            "event_type": event.event_type,
            "task_id": event.task_id,
            "message_id": event.message_id,
            "recipient_id": event.recipient_id,
            "round_number": event.round_number,
            "payload": copy.deepcopy(dict(event.payload)),
        })
        self.idempotency[event.idempotency_key] = {
            "fingerprint": event.request_fingerprint,
            "version": version,
        }
        return {"replayed": False, "version": version}

    async def list_events(self, *, mission_id, scope_type, workspace_id=None, limit=100):
        if mission_id != self.mission_id or workspace_id != self.workspace_id:
            return []
        return copy.deepcopy(self.events[:limit])


class FakeNativeAdapter:
    max_concurrency = 1
    timeout_seconds = 5.0

    def __init__(self, engine_id, summary, capability="research.analyze"):
        self.engine_id = engine_id
        self.capabilities = frozenset({capability})
        self.summary = summary
        self.calls = []

    async def invoke(self, context):
        self.calls.append(context)
        return EngineContribution(
            engine_id=self.engine_id,
            capability=context.capability,
            task_id=context.task_id,
            mission_id=context.mission_id,
            workspace_id=context.workspace_id,
            status="completed",
            summary=self.summary,
            findings=(f"{self.engine_id} finding",),
            recommendations=(f"{self.engine_id} recommendation",),
            evidence=({"ref": f"source:{self.engine_id}"},),
            confidence=0.84,
            source_metadata={"providers": ["test-provider"], "usage_tokens": 125},
            correlation_id=context.correlation_id,
        )


@pytest.mark.asyncio
async def test_native_engine_execution_requires_persisted_approval_and_records_full_lifecycle():
    store = MemoryMissionStore()
    registry = AdapterRegistry()
    researcher = FakeNativeAdapter("content_intelligence", "Research completed with evidence.")
    reviewer = FakeNativeAdapter("meta_engine", "Independent review supports the finding.", capability="review.analyze")
    registry.register(researcher)
    registry.register(reviewer)
    task_state_changes = []
    runtime = MissionOrchestrationRuntime(
        store=store,
        adapters=registry,
        task_state_callback=lambda task_id, status, result, error: task_state_changes.append((task_id, status)),
    )

    await runtime.assign_task(
        mission_id=store.mission_id,
        workspace_id=store.workspace_id,
        task_id=store.task_id,
        engine_id="content_intelligence",
        capability="research.analyze",
        idempotency_key="assign-1",
        correlation_id="corr-runtime-1",
    )

    with pytest.raises(MissionTaskNotReady, match="canonical_director_approval_not_persisted"):
        await runtime.approve_task(
            mission_id=store.mission_id,
            workspace_id=store.workspace_id,
            task_id=store.task_id,
            operator_id="operator-1",
            idempotency_key="approve-before-save",
            correlation_id="corr-runtime-1",
        )

    store.task["status"] = "approved"
    store.task["payload"]["status"] = "approved"
    store.task["payload"]["approved_by"] = "operator-1"
    await runtime.approve_task(
        mission_id=store.mission_id,
        workspace_id=store.workspace_id,
        task_id=store.task_id,
        operator_id="operator-1",
        idempotency_key="approve-1",
        correlation_id="corr-runtime-1",
    )

    result = await runtime.execute_approved_task(
        mission_id=store.mission_id,
        workspace_id=store.workspace_id,
        task_id=store.task_id,
        engine_id="content_intelligence",
        objective="Research the approved task.",
        inputs={"topic": "workspace growth"},
        idempotency_prefix="execute-1",
        correlation_id="corr-runtime-1",
    )
    assert result.status == "completed"
    assert len(researcher.calls) == 1
    assert store.task["status"] == "completed"
    assert store.row["state_snapshot"]["task_results"][store.task_id]["summary"] == result.summary
    with pytest.raises(MissionTaskNotReady, match="canonical_director_approval_not_persisted"):
        await runtime.execute_approved_task(
            mission_id=store.mission_id,
            workspace_id=store.workspace_id,
            task_id=store.task_id,
            engine_id="content_intelligence",
            objective="Do not execute a completed task again.",
            inputs={},
            idempotency_prefix="execute-duplicate",
            correlation_id="corr-runtime-1",
        )
    assert len(researcher.calls) == 1

    await runtime.ask_engine(
        mission_id=store.mission_id,
        workspace_id=store.workspace_id,
        task_id=store.task_id,
        actor_engine_id="content_intelligence",
        recipient_engine_id="meta_engine",
        question="Independently challenge the source quality.",
        message_id="question-1",
        idempotency_key="question-event-1",
        correlation_id="corr-runtime-1",
        round_number=1,
        context="Compare the cited evidence with the finding.",
        recipient_capability="review.analyze",
    )
    answer = await runtime.answer_engine_question(
        mission_id=store.mission_id,
        workspace_id=store.workspace_id,
        question_message_id="question-1",
        answer_message_id="answer-1",
        correlation_id="corr-runtime-1",
        idempotency_prefix="answer-event-1",
    )
    assert answer.engine_id == "meta_engine"
    assert len(reviewer.calls) == 1
    assert reviewer.calls[0].capability == "review.analyze"
    assert store.row["state_snapshot"]["pending_questions"] == {}
    task_evidence = store.row["state_snapshot"]["task_results"][store.task_id]["evidence_refs"]
    assert "source:content_intelligence" in task_evidence
    assert "source:meta_engine" in task_evidence

    await runtime.verify_task(
        mission_id=store.mission_id,
        workspace_id=store.workspace_id,
        task_id=store.task_id,
        verification_ref="director-verification-1",
        idempotency_key="verify-1",
        correlation_id="corr-runtime-1",
    )
    assert store.task["status"] == "verified"
    assert [status for _, status in task_state_changes] == ["executing", "completed", "verified"]
    completed = await runtime.complete_mission(
        mission_id=store.mission_id,
        workspace_id=store.workspace_id,
        idempotency_key="mission-complete-1",
        correlation_id="corr-runtime-1",
    )
    assert completed.status == "completed"

    event_types = [event["event_type"] for event in store.events]
    assert "task.started" in event_types
    assert "engine.evidence.submitted" in event_types
    assert "task.completed" in event_types
    assert "engine.question.asked" in event_types
    assert "engine.answer.received" in event_types
    assert "task.verified" in event_types
    assert event_types[-1] == "mission.completed"


@pytest.mark.asyncio
async def test_runtime_cannot_assign_task_to_unregistered_engine_or_cross_workspace():
    store = MemoryMissionStore()
    registry = AdapterRegistry()
    registry.register(FakeNativeAdapter("content_intelligence", "Research completed."))
    runtime = MissionOrchestrationRuntime(store=store, adapters=registry)

    with pytest.raises(KeyError, match="native_engine_adapter_unavailable"):
        await runtime.assign_task(
            mission_id=store.mission_id,
            workspace_id=store.workspace_id,
            task_id=store.task_id,
            engine_id="unknown_engine",
            capability="research.analyze",
            idempotency_key="assign-unknown",
            correlation_id="corr-runtime-2",
        )

    with pytest.raises(MissionRuntimeNotFound, match="workspace_mission_not_found"):
        await runtime.assign_task(
            mission_id=store.mission_id,
            workspace_id="another-workspace",
            task_id=store.task_id,
            engine_id="content_intelligence",
            capability="research.analyze",
            idempotency_key="assign-cross-tenant",
            correlation_id="corr-runtime-2",
        )



def test_mission_runtime_store_reads_canonical_task_only_with_workspace_and_mission_scope():
    from types import SimpleNamespace

    class Query:
        def __init__(self, owner, table):
            self.owner = owner
            self.table = table
            self.filters = []
            self.limit_value = None

        def select(self, *args, **kwargs):
            return self

        def eq(self, key, value):
            self.filters.append((key, value))
            return self

        def limit(self, value):
            self.limit_value = value
            return self

        def execute(self):
            row = self.owner.rows.get(self.table)
            if row is None or any(row.get(key) != value for key, value in self.filters):
                return SimpleNamespace(data=[])
            return SimpleNamespace(data=[copy.deepcopy(row)])

    class Supabase:
        def __init__(self):
            self.rows = {
                "director_tasks": {
                    "id": "task-store-runtime",
                    "workspace_id": "workspace-store-runtime",
                    "scope_type": "workspace",
                    "status": "approved",
                    "payload": {
                        "id": "task-store-runtime",
                        "workspace_id": "workspace-store-runtime",
                        "scope_type": "workspace",
                        "mission_id": "mission-store-runtime",
                        "status": "approved",
                        "approved_by": "operator-1",
                    },
                }
            }

        def table(self, name):
            return Query(self, name)

    class Backend:
        def __init__(self):
            self.supabase = Supabase()

        async def _execute_with_retry(self, operation, fn):
            return fn()

    async def run():
        store = MissionRuntimeStore(Backend())
        row = await store.get_workspace_task(
            task_id="task-store-runtime",
            workspace_id="workspace-store-runtime",
            mission_id="mission-store-runtime",
        )
        assert row["status"] == "approved"
        assert await store.get_workspace_task(
            task_id="task-store-runtime",
            workspace_id="different-workspace",
            mission_id="mission-store-runtime",
        ) is None
        assert await store.get_workspace_task(
            task_id="task-store-runtime",
            workspace_id="workspace-store-runtime",
            mission_id="different-mission",
        ) is None

    import asyncio
    asyncio.run(run())


@pytest.mark.asyncio
async def test_condition_engine_adapter_is_workspace_verified_advisory_only():
    now = datetime.now(timezone.utc).isoformat()

    class FakeConditionEngine:
        def __init__(self):
            self.calls = []

        def calculate_brand_health(self, brand_id, *, emit_event=True, workspace_id=None):
            self.calls.append((brand_id, emit_event, workspace_id))
            return {
                "score": 74.5,
                "status": "stable",
                "components": {"engagement": {"combined": 72.0}},
                "observation_count": 4,
                "latest_observed_at": now,
                "observation_refs": [
                    {"ref": "condition-observation:obs-1", "post_id": "post-1", "observed_at": now}
                ],
            }

    engine = FakeConditionEngine()
    verifier_calls = []

    async def verify_brand(brand_id, workspace_id):
        verifier_calls.append((brand_id, workspace_id))
        return brand_id == "brand-owned" and workspace_id == "workspace-runtime-test"

    adapter = ConditionEngineMissionAdapter(
        engine=engine,
        brand_ownership_verifier=verify_brand,
    )
    context = MissionTaskContext(
        task_id="condition-task",
        mission_id="condition-mission",
        workspace_id="workspace-runtime-test",
        capability="calculate_brand_health",
        objective="Assess brand health using existing observations.",
        inputs={"brand_id": "brand-owned"},
        correlation_id="condition-correlation",
    )
    result = await adapter.invoke(context)

    assert result.status == "completed"
    assert result.engine_id == "condition_engine"
    assert result.side_effect_class == "internal_record"
    assert result.authority == "none"
    assert result.evidence[0]["ref"] == "condition-observation:obs-1"
    assert engine.calls == [("brand-owned", False, "workspace-runtime-test")]
    assert verifier_calls == [("brand-owned", "workspace-runtime-test")]

    denied = ConditionEngineMissionAdapter(
        engine=engine,
        brand_ownership_verifier=lambda brand_id, workspace_id: False,
    )
    with pytest.raises(PermissionError, match="brand_workspace_ownership_failed"):
        await denied.invoke(context)


def test_default_registry_requires_explicit_workspace_brand_verifier():
    class FakeConditionEngine:
        def calculate_brand_health(self, brand_id, *, emit_event=True, workspace_id=None):
            return {"score": 50, "status": "unknown", "observation_count": 0}

    class DirectorStore:
        async def brand_belongs_to_workspace(self, brand_id, workspace_id):
            return True

    registry = build_default_adapter_registry(
        director_store=DirectorStore(),
        condition_engine=FakeConditionEngine(),
    )
    descriptions = {item["engine_id"]: item for item in registry.describe()}
    assert set(descriptions) == {"content_intelligence", "condition_engine"}
    assert "calculate_brand_health" in descriptions["condition_engine"]["capabilities"]
    assert descriptions["condition_engine"]["authority"] == "none"

    with pytest.raises(ValueError, match="director_store_brand_ownership_verifier_required"):
        build_default_adapter_registry(director_store=object(), condition_engine=FakeConditionEngine())



def test_condition_engine_brand_health_snapshot_is_locked_and_workspace_scoped(monkeypatch):
    import services.condition_engine.core as condition_core
    from services.condition_engine.models import Observation

    engine = condition_core.AutonomousConditionEngine(supabase_client=None)
    observation = Observation(
        post_id="post-condition-1",
        brand_id="brand-condition-1",
        likes=30,
        comments=5,
        shares=4,
        clicks=8,
        impressions=1000,
        reach=900,
        topic="education",
        correlation_id="condition-observation-1",
    ).normalize()
    engine._observations["brand-condition-1"].append(observation)

    published = []
    monkeypatch.setattr(condition_core, "_publish_event", lambda name, payload: published.append((name, payload)))

    health = engine.calculate_brand_health(
        "brand-condition-1",
        emit_event=False,
        workspace_id="workspace-condition-1",
    )
    assert health["observation_count"] == 1
    assert health["observation_refs"][0]["ref"] == "condition-observation:condition-observation-1"
    assert published == []

    engine.calculate_brand_health(
        "brand-condition-1",
        emit_event=True,
        workspace_id="workspace-condition-1",
    )
    assert published[-1][0] == "brand_health_updated"
    assert published[-1][1]["workspace_id"] == "workspace-condition-1"
