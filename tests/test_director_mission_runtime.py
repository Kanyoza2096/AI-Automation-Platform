import asyncio
import copy
from datetime import datetime, timedelta, timezone
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
    MetaEngineMissionAdapter,
    assess_execution_recovery,
)


class MemoryMissionStore:
    MAX_EVENT_READ_LIMIT = 500

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
        self.fail_atomic_approval = False
        # Model the database row-lock boundary for concurrent approval tests.
        self._approval_lock = asyncio.Lock()

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

    async def approve_workspace_task_atomic(
        self, *, event, task_id, approved_by, approved_at
    ):
        # The production RPC serializes contenders by locking mission then task.
        # Yield while holding this lock so the test exercises a real async race.
        async with self._approval_lock:
            await asyncio.sleep(0)
            return await self._approve_workspace_task_atomic_unlocked(
                event=event, task_id=task_id,
                approved_by=approved_by, approved_at=approved_at,
            )

    async def _approve_workspace_task_atomic_unlocked(
        self, *, event, task_id, approved_by, approved_at
    ):
        if self.fail_atomic_approval:
            raise RuntimeError("simulated_atomic_approval_rpc_failure")
        existing = self.idempotency.get(event.idempotency_key)
        if existing:
            durable = next(
                (item for item in self.events if item["idempotency_key"] == event.idempotency_key),
                None,
            )
            if (
                existing["fingerprint"] != event.request_fingerprint
                or not durable
                or durable["actor_type"] != "operator"
                or durable["actor_id"] != approved_by
                or durable["payload"].get("approved_at") != approved_at
                or self.task["status"] != "approved"
                or self.task["payload"].get("approved_by") != approved_by
                or self.task["payload"].get("approved_at") != approved_at
            ):
                return {"error": "director_atomic_task_approval_replay_conflict"}
            return {"replayed": True, "version": self.row["version"]}
        if self.task["status"] != "approval_required":
            return {"error": "director_atomic_task_approval_state_conflict"}
        if (self.row["state_snapshot"].get("task_status") or {}).get(task_id) != "approval_required":
            return {"error": "director_atomic_task_approval_state_conflict"}

        # Simulate a single transaction: restore all durable structures if
        # event append fails, and publish task provenance only on success.
        previous_row = copy.deepcopy(self.row)
        previous_task = copy.deepcopy(self.task)
        previous_events = copy.deepcopy(self.events)
        previous_idempotency = copy.deepcopy(self.idempotency)
        result = await self.append_event(event)
        if result.get("error"):
            self.row = previous_row
            self.task = previous_task
            self.events = previous_events
            self.idempotency = previous_idempotency
            return result
        self.task["status"] = "approved"
        self.task["payload"].update({
            "status": "approved",
            "approved_by": approved_by,
            "approved_at": approved_at,
        })
        return result | {
            "task_id": task_id,
            "task_status": "approved",
            "approved_by": approved_by,
            "approved_at": approved_at,
        }

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
            "idempotency_key": event.idempotency_key,
            "actor_type": event.actor_type,
            "actor_id": event.actor_id,
            "request_fingerprint": event.request_fingerprint,
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

    async def find_event(
        self, *, mission_id, scope_type, workspace_id, idempotency_key, event_type, task_id
    ):
        if mission_id != self.mission_id or workspace_id != self.workspace_id:
            return None
        for event in self.events:
            if (
                event.get("idempotency_key") == idempotency_key
                and event.get("event_type") == event_type
                and event.get("task_id") == task_id
            ):
                return copy.deepcopy(event)
        return None

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
async def test_meta_engine_adapter_uses_fresh_read_only_snapshot_and_marks_stale_data_partial():
    class FakeMetaEngine:
        def __init__(self, timestamp):
            self.timestamp = timestamp
            self.calls = 0

        def get_status(self):
            self.calls += 1
            return {
                "timestamp": self.timestamp,
                "overall": "degraded",
                "health_score": 62.0,
                "risk_score": 31.0,
                "active_incidents": 2,
                "monitoring": {"running": True},
                "engines": {
                    "job_service": {"status": "degraded", "health_score": 55.0},
                    "content_engine": {"status": "healthy", "health_score": 90.0},
                },
            }

    now = datetime.now(timezone.utc).isoformat()
    engine = FakeMetaEngine(now)
    adapter = MetaEngineMissionAdapter(engine)
    context = MissionTaskContext(
        task_id="meta-task",
        mission_id="meta-mission",
        workspace_id="workspace-runtime-test",
        capability="platform.health.snapshot",
        objective="Review the platform health snapshot.",
        inputs={},
        correlation_id="meta-correlation",
    )
    result = await adapter.invoke(context)
    assert result.status == "completed"
    assert result.engine_id == "meta_engine"
    assert result.authority == "none"
    assert result.source_metadata["recovery_invoked"] is False
    assert result.evidence[0]["ref"].startswith("meta_engine:snapshot:")
    assert any(item.startswith("job_service: degraded") for item in result.findings)
    assert engine.calls == 1

    stale = MetaEngineMissionAdapter(FakeMetaEngine("2020-01-01T00:00:00+00:00"))
    stale_result = await stale.invoke(context)
    assert stale_result.status == "partial"
    assert stale_result.source_metadata["freshness_window_seconds"] == 300


@pytest.mark.asyncio
async def test_atomic_approval_failure_leaves_task_and_mission_journal_unchanged():
    store = MemoryMissionStore()
    runtime = MissionOrchestrationRuntime(store=store, adapters=AdapterRegistry())
    await runtime.assign_task(
        mission_id=store.mission_id,
        workspace_id=store.workspace_id,
        task_id=store.task_id,
        engine_id="content_intelligence",
        capability="research.analyze",
        idempotency_key="assign-atomic-failure",
        correlation_id="corr-atomic-failure",
    )
    before_row = copy.deepcopy(store.row)
    before_task = copy.deepcopy(store.task)
    before_events = copy.deepcopy(store.events)
    store.fail_atomic_approval = True

    with pytest.raises(RuntimeError, match="simulated_atomic_approval_rpc_failure"):
        await runtime.approve_task(
            mission_id=store.mission_id,
            workspace_id=store.workspace_id,
            task_id=store.task_id,
            operator_id="operator-failure",
            approved_at=datetime(2026, 11, 19, 12, 20, tzinfo=timezone.utc),
            idempotency_key="approval-failure",
            correlation_id="corr-atomic-failure",
        )

    assert store.task == before_task
    assert store.row == before_row
    assert store.events == before_events


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

    approved_at = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
    await runtime.approve_task(
        mission_id=store.mission_id,
        workspace_id=store.workspace_id,
        task_id=store.task_id,
        operator_id="operator-1",
        approved_at=approved_at,
        idempotency_key=f"director-task-approval:{store.task_id}:{approved_at.isoformat()}",
        correlation_id="corr-runtime-1",
    )
    assert store.task["status"] == "approved"
    assert store.task["payload"]["approved_by"] == "operator-1"
    assert store.task["payload"]["approved_at"] == approved_at.isoformat()
    assert store.row["state_snapshot"]["task_status"][store.task_id] == "approved"

    # Simulate a lost RPC response: process memory generates a new timestamp,
    # but runtime recovers the original durable actor/timestamp/event.
    approved_event_count = sum(event["event_type"] == "task.approved" for event in store.events)
    replayed = await runtime.approve_task(
        mission_id=store.mission_id,
        workspace_id=store.workspace_id,
        task_id=store.task_id,
        operator_id="operator-1",
        approved_at=datetime(2026, 11, 19, 12, 5, tzinfo=timezone.utc),
        idempotency_key="retry-with-new-request-timestamp",
        correlation_id="corr-runtime-1",
    )
    assert replayed.version == store.row["version"]
    assert sum(event["event_type"] == "task.approved" for event in store.events) == approved_event_count == 1
    with pytest.raises(MissionTaskNotReady, match="canonical_approval_provenance_conflict"):
        await runtime.approve_task(
            mission_id=store.mission_id,
            workspace_id=store.workspace_id,
            task_id=store.task_id,
            operator_id="operator-2",
            approved_at=approved_at,
            idempotency_key="conflicting-operator",
            correlation_id="corr-runtime-1",
        )

    result = await runtime.execute_approved_task(
        mission_id=store.mission_id,
        workspace_id=store.workspace_id,
        task_id=store.task_id,
        engine_id="content_intelligence",
        objective="Research the approved task.",
        inputs={"topic": "workspace growth"},
        operator_id="operator-1",
        idempotency_prefix="execute-1",
        correlation_id="corr-runtime-1",
    )
    assert result.status == "completed"
    started_event = next(event for event in store.events if event["event_type"] == "task.started")
    assert started_event["actor_type"] == "operator"
    assert started_event["actor_id"] == "operator-1"
    assert started_event["payload"]["requested_by"] == "operator-1"
    assert len(researcher.calls) == 1
    assert store.task["status"] == "completed"
    assert store.row["state_snapshot"]["task_results"][store.task_id]["summary"] == result.summary
    assert not any(event["event_type"] == "engine.call.completed" for event in store.events)
    assert store.row["state_snapshot"]["budget"]["provider_calls"] == 1
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
async def test_native_engine_invocation_timeout_fails_task_without_hanging():
    import asyncio

    class SlowAdapter(FakeNativeAdapter):
        async def invoke(self, context):
            self.calls.append(context)
            await asyncio.sleep(1)
            raise AssertionError("timeout should cancel native adapter invocation")

    store = MemoryMissionStore()
    registry = AdapterRegistry()
    slow = SlowAdapter("content_intelligence", "This should never return.")
    registry.register(slow)
    runtime = MissionOrchestrationRuntime(
        store=store,
        adapters=registry,
        task_state_callback=lambda *args: None,
        default_timeout_seconds=0.01,
    )
    await runtime.assign_task(
        mission_id=store.mission_id,
        workspace_id=store.workspace_id,
        task_id=store.task_id,
        engine_id="content_intelligence",
        capability="research.analyze",
        idempotency_key="assign-timeout",
        correlation_id="corr-timeout",
    )
    approved_at = datetime(2026, 11, 19, 12, 10, tzinfo=timezone.utc)
    await runtime.approve_task(
        mission_id=store.mission_id,
        workspace_id=store.workspace_id,
        task_id=store.task_id,
        operator_id="operator-timeout",
        approved_at=approved_at,
        idempotency_key=f"director-task-approval:{store.task_id}:{approved_at.isoformat()}",
        correlation_id="corr-timeout",
    )

    with pytest.raises(asyncio.TimeoutError):
        await runtime.execute_approved_task(
            mission_id=store.mission_id,
            workspace_id=store.workspace_id,
            task_id=store.task_id,
            engine_id="content_intelligence",
            objective="Exercise the bounded invocation timeout.",
            inputs={},
            operator_id="operator-timeout",
            idempotency_prefix="execute-timeout",
            correlation_id="corr-timeout",
            timeout_seconds=0.01,
        )

    started_event = next(event for event in store.events if event["event_type"] == "task.started")
    assert started_event["actor_type"] == "operator"
    assert started_event["actor_id"] == "operator-timeout"
    assert len(slow.calls) == 1
    assert store.task["status"] == "failed"
    assert any(event["event_type"] == "task.failed" for event in store.events)
    with pytest.raises(MissionTaskNotReady, match="canonical_director_approval_not_persisted"):
        await runtime.execute_approved_task(
            mission_id=store.mission_id,
            workspace_id=store.workspace_id,
            task_id=store.task_id,
            engine_id="content_intelligence",
            objective="A timed-out task must not be blindly re-dispatched.",
            inputs={},
            operator_id="operator-timeout",
            idempotency_prefix="execute-timeout-retry",
            correlation_id="corr-timeout",
            timeout_seconds=0.01,
        )
    assert len(slow.calls) == 1


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

    class FakeMetaEngine:
        def get_status(self):
            return {"timestamp": datetime.now(timezone.utc).isoformat(), "overall": "nominal", "engines": {}}

    registry = build_default_adapter_registry(
        director_store=DirectorStore(),
        condition_engine=FakeConditionEngine(),
        meta_engine=FakeMetaEngine(),
    )
    descriptions = {item["engine_id"]: item for item in registry.describe()}
    assert set(descriptions) == {"content_intelligence", "condition_engine", "meta_engine"}
    assert "platform.health.snapshot" in descriptions["meta_engine"]["capabilities"]
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


@pytest.mark.asyncio
async def test_native_execution_rejects_dependencies_until_canonical_task_is_completed():
    store = MemoryMissionStore()
    dependency_id = "task-dependency"
    store.task["payload"]["depends_on"] = [dependency_id]
    original_get_task = store.get_workspace_task

    async def get_task_with_dependency(*, task_id, workspace_id, mission_id):
        if task_id == dependency_id:
            return {
                "id": dependency_id,
                "scope_type": "workspace",
                "workspace_id": workspace_id,
                "status": "approved",
                "payload": {
                    "id": dependency_id,
                    "mission_id": mission_id,
                    "workspace_id": workspace_id,
                    "status": "approved",
                },
            }
        return await original_get_task(
            task_id=task_id, workspace_id=workspace_id, mission_id=mission_id
        )

    store.get_workspace_task = get_task_with_dependency
    registry = AdapterRegistry()
    adapter = FakeNativeAdapter("content_intelligence", "Should not execute.")
    registry.register(adapter)
    runtime = MissionOrchestrationRuntime(
        store=store, adapters=registry, task_state_callback=lambda *args: None
    )
    await runtime.assign_task(
        mission_id=store.mission_id,
        workspace_id=store.workspace_id,
        task_id=store.task_id,
        engine_id="content_intelligence",
        capability="research.analyze",
        idempotency_key="assign-with-dependency",
        correlation_id="corr-dependency",
    )
    approved_at = datetime(2026, 11, 19, 12, 15, tzinfo=timezone.utc)
    await runtime.approve_task(
        mission_id=store.mission_id,
        workspace_id=store.workspace_id,
        task_id=store.task_id,
        operator_id="operator-dependency",
        approved_at=approved_at,
        idempotency_key=f"director-task-approval:{store.task_id}:{approved_at.isoformat()}",
        correlation_id="corr-dependency",
    )

    with pytest.raises(MissionTaskNotReady, match="mission_dependency_not_completed"):
        await runtime.execute_approved_task(
            mission_id=store.mission_id,
            workspace_id=store.workspace_id,
            task_id=store.task_id,
            engine_id="content_intelligence",
            objective="Must wait for its dependency.",
            inputs={},
            idempotency_prefix="execute-with-dependency",
            correlation_id="corr-dependency",
        )
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_runtime_cancels_pending_task_and_atomically_updates_canonical_status():
    store = MemoryMissionStore()
    callback_calls = []
    runtime = MissionOrchestrationRuntime(
        store=store,
        adapters=AdapterRegistry(),
        task_state_callback=lambda *args: callback_calls.append(args),
    )

    snapshot = await runtime.cancel_task(
        mission_id=store.mission_id,
        workspace_id=store.workspace_id,
        task_id=store.task_id,
        operator_id="operator-cancel",
        reason="Rejected by operator-cancel: evidence was insufficient",
        idempotency_key="cancel-pending-task",
        correlation_id="corr-cancel",
    )

    assert store.task["status"] == "cancelled"
    assert store.task["payload"]["status"] == "cancelled"
    assert store.task["payload"]["error"] == "Rejected by operator-cancel: evidence was insufficient"
    assert snapshot.state_snapshot["task_status"][store.task_id] == "cancelled"
    assert snapshot.status == "awaiting_review"
    assert store.events[-1]["event_type"] == "task.cancelled"
    assert callback_calls == [(
        store.task_id,
        "cancelled",
        None,
        "Rejected by operator-cancel: evidence was insufficient",
    )]


@pytest.mark.asyncio
async def test_runtime_refuses_to_cancel_task_after_execution_has_started():
    store = MemoryMissionStore()
    store.task["status"] = "executing"
    store.task["payload"]["status"] = "executing"
    runtime = MissionOrchestrationRuntime(
        store=store,
        adapters=AdapterRegistry(),
        task_state_callback=lambda *args: None,
    )

    with pytest.raises(MissionTaskNotReady, match="task_not_cancellable_from_canonical_status"):
        await runtime.cancel_task(
            mission_id=store.mission_id,
            workspace_id=store.workspace_id,
            task_id=store.task_id,
            operator_id="operator-cancel",
            reason="Too late to cancel",
            idempotency_key="cancel-executing-task",
            correlation_id="corr-cancel",
        )

    assert store.events == []
    assert store.task["status"] == "executing"


@pytest.mark.asyncio
async def test_atomic_approval_retry_rejects_durable_timestamp_provenance_mismatch():
    store = MemoryMissionStore()
    runtime = MissionOrchestrationRuntime(store=store, adapters=AdapterRegistry())
    await runtime.assign_task(
        mission_id=store.mission_id,
        workspace_id=store.workspace_id,
        task_id=store.task_id,
        engine_id="content_intelligence",
        capability="research.analyze",
        idempotency_key="assign-approval-timestamp-mismatch",
        correlation_id="corr-approval-timestamp-mismatch",
    )
    approved_at = datetime(2026, 11, 19, 12, 30, tzinfo=timezone.utc)
    await runtime.approve_task(
        mission_id=store.mission_id,
        workspace_id=store.workspace_id,
        task_id=store.task_id,
        operator_id="operator-timestamp",
        approved_at=approved_at,
        idempotency_key=f"director-task-approval:{store.task_id}:{approved_at.isoformat()}",
        correlation_id="corr-approval-timestamp-mismatch",
    )

    approval_event = next(
        event for event in store.events if event["event_type"] == "task.approved"
    )
    approval_event["payload"]["approved_at"] = datetime(
        2026, 11, 19, 12, 31, tzinfo=timezone.utc
    ).isoformat()

    with pytest.raises(
        MissionTaskNotReady,
        match="canonical_approval_journal_provenance_conflict",
    ):
        await runtime.approve_task(
            mission_id=store.mission_id,
            workspace_id=store.workspace_id,
            task_id=store.task_id,
            operator_id="operator-timestamp",
            approved_at=datetime(2026, 11, 19, 12, 32, tzinfo=timezone.utc),
            idempotency_key="retry-after-timestamp-provenance-tamper",
            correlation_id="corr-approval-timestamp-mismatch",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("corruption", "expected_error"),
    [
        ("mission_snapshot", "canonical_approval_provenance_conflict"),
        ("missing_event", "canonical_approval_journal_provenance_conflict"),
        ("event_actor", "canonical_approval_journal_provenance_conflict"),
        ("task_actor", "canonical_approval_provenance_conflict"),
    ],
)
async def test_approval_retry_fails_closed_on_canonical_journal_disagreement(
    corruption, expected_error
):
    store = MemoryMissionStore()
    runtime = MissionOrchestrationRuntime(store=store, adapters=AdapterRegistry())
    await runtime.assign_task(
        mission_id=store.mission_id,
        workspace_id=store.workspace_id,
        task_id=store.task_id,
        engine_id="content_intelligence",
        capability="research.analyze",
        idempotency_key=f"assign-reconcile-{corruption}",
        correlation_id=f"corr-reconcile-{corruption}",
    )
    approved_at = datetime(2026, 11, 19, 13, 15, tzinfo=timezone.utc)
    approval_key = f"director-task-approval:{store.task_id}:{approved_at.isoformat()}"
    await runtime.approve_task(
        mission_id=store.mission_id,
        workspace_id=store.workspace_id,
        task_id=store.task_id,
        operator_id="operator-reconcile",
        approved_at=approved_at,
        idempotency_key=approval_key,
        correlation_id=f"corr-reconcile-{corruption}",
    )

    approval_event = next(
        event for event in store.events if event["event_type"] == "task.approved"
    )
    if corruption == "mission_snapshot":
        store.row["state_snapshot"]["task_status"][store.task_id] = "approval_required"
    elif corruption == "missing_event":
        store.events.remove(approval_event)
    elif corruption == "event_actor":
        approval_event["actor_id"] = "different-operator"
    elif corruption == "task_actor":
        store.task["payload"]["approved_by"] = "different-operator"

    before_task = copy.deepcopy(store.task)
    before_snapshot = copy.deepcopy(store.row["state_snapshot"])
    before_events = copy.deepcopy(store.events)

    with pytest.raises(MissionTaskNotReady, match=expected_error):
        await runtime.approve_task(
            mission_id=store.mission_id,
            workspace_id=store.workspace_id,
            task_id=store.task_id,
            operator_id="operator-reconcile",
            approved_at=datetime(2026, 11, 19, 13, 16, tzinfo=timezone.utc),
            idempotency_key="retry-after-canonical-journal-disagreement",
            correlation_id=f"corr-reconcile-{corruption}",
        )

    # Recovery is deliberately read-only: disagreement is surfaced, never
    # silently repaired or converted into a new operator approval.
    assert store.task == before_task
    assert store.row["state_snapshot"] == before_snapshot
    assert store.events == before_events



@pytest.mark.asyncio
async def test_concurrent_approvals_for_one_task_commit_exactly_one_operator_decision():
    store = MemoryMissionStore()
    runtime = MissionOrchestrationRuntime(store=store, adapters=AdapterRegistry())
    await runtime.assign_task(
        mission_id=store.mission_id,
        workspace_id=store.workspace_id,
        task_id=store.task_id,
        engine_id="content_intelligence",
        capability="research.analyze",
        idempotency_key="assign-concurrent-approval",
        correlation_id="corr-concurrent-approval",
    )

    approved_at_a = datetime(2026, 11, 19, 13, 0, tzinfo=timezone.utc)
    approved_at_b = datetime(2026, 11, 19, 13, 0, 1, tzinfo=timezone.utc)

    results = await asyncio.gather(
        runtime.approve_task(
            mission_id=store.mission_id,
            workspace_id=store.workspace_id,
            task_id=store.task_id,
            operator_id="operator-a",
            approved_at=approved_at_a,
            idempotency_key=f"director-task-approval:{store.task_id}:{approved_at_a.isoformat()}",
            correlation_id="corr-concurrent-approval-a",
        ),
        runtime.approve_task(
            mission_id=store.mission_id,
            workspace_id=store.workspace_id,
            task_id=store.task_id,
            operator_id="operator-b",
            approved_at=approved_at_b,
            idempotency_key=f"director-task-approval:{store.task_id}:{approved_at_b.isoformat()}",
            correlation_id="corr-concurrent-approval-b",
        ),
        return_exceptions=True,
    )

    successes = [item for item in results if not isinstance(item, Exception)]
    failures = [item for item in results if isinstance(item, Exception)]
    approval_events = [
        event for event in store.events if event["event_type"] == "task.approved"
    ]

    assert len(successes) == 1
    assert len(failures) == 1
    assert len(approval_events) == 1
    assert store.task["status"] == "approved"
    assert store.task["payload"]["approved_by"] in {"operator-a", "operator-b"}
    assert store.task["payload"]["approved_at"] in {
        approved_at_a.isoformat(),
        approved_at_b.isoformat(),
    }
    assert approval_events[0]["actor_id"] == store.task["payload"]["approved_by"]
    assert approval_events[0]["payload"]["approved_at"] == store.task["payload"]["approved_at"]


@pytest.mark.asyncio
async def test_concurrent_native_execution_dispatches_engine_only_once():
    store = MemoryMissionStore()
    registry = AdapterRegistry()
    entered = asyncio.Event()
    release = asyncio.Event()

    class BlockingNativeAdapter(FakeNativeAdapter):
        async def invoke(self, context):
            self.calls.append(context)
            entered.set()
            await release.wait()
            return EngineContribution(
                engine_id=self.engine_id,
                capability=context.capability,
                task_id=context.task_id,
                mission_id=context.mission_id,
                workspace_id=context.workspace_id,
                status="completed",
                summary="Single durable execution completed.",
                evidence=({"ref": "source:single-execution"},),
            )

    researcher = BlockingNativeAdapter("content_intelligence", "Single durable execution completed.")
    registry.register(researcher)
    runtime = MissionOrchestrationRuntime(
        store=store,
        adapters=registry,
        task_state_callback=lambda *_args: None,
    )
    await runtime.assign_task(
        mission_id=store.mission_id,
        workspace_id=store.workspace_id,
        task_id=store.task_id,
        engine_id="content_intelligence",
        capability="research.analyze",
        idempotency_key="assign-concurrent-exec",
        correlation_id="corr-concurrent-exec",
    )
    approved_at = datetime(2026, 11, 19, 13, 0, tzinfo=timezone.utc)
    await runtime.approve_task(
        mission_id=store.mission_id,
        workspace_id=store.workspace_id,
        task_id=store.task_id,
        operator_id="operator-1",
        approved_at=approved_at,
        idempotency_key=f"director-task-approval:{store.task_id}:{approved_at.isoformat()}",
        correlation_id="corr-concurrent-exec",
    )

    first = asyncio.create_task(runtime.execute_approved_task(
        mission_id=store.mission_id,
        workspace_id=store.workspace_id,
        task_id=store.task_id,
        engine_id="content_intelligence",
        objective="Run the approved task once.",
        inputs={},
        operator_id="operator-1",
        idempotency_prefix="concurrent-exec-1",
        correlation_id="corr-concurrent-exec",
    ))
    await asyncio.wait_for(entered.wait(), timeout=2)

    with pytest.raises(MissionTaskNotReady, match="canonical_director_approval_not_persisted"):
        await runtime.execute_approved_task(
            mission_id=store.mission_id,
            workspace_id=store.workspace_id,
            task_id=store.task_id,
            engine_id="content_intelligence",
            objective="Attempt a concurrent duplicate.",
            inputs={},
            idempotency_prefix="concurrent-exec-2",
            correlation_id="corr-concurrent-exec",
        )

    release.set()
    result = await first
    assert result.status == "completed"
    assert len(researcher.calls) == 1
    assert store.task["status"] == "completed"
    assert [event["event_type"] for event in store.events].count("task.started") == 1
    assert [event["event_type"] for event in store.events].count("task.completed") == 1


def _recovery_task(status="executing"):
    return {
        "id": "task-recovery",
        "scope_type": "workspace",
        "workspace_id": "workspace-recovery",
        "status": status,
        "payload": {
            "scope_type": "workspace",
            "workspace_id": "workspace-recovery",
            "mission_id": "mission-recovery",
            "status": status,
        },
    }


def _recovery_event(event_type, key, *, created_at="2026-11-19T10:00:00+00:00", payload=None, event_id=None, actor_type=None, actor_id=None):
    return {
        "event_id": event_id or key,
        "mission_id": "mission-recovery",
        "scope_type": "workspace",
        "workspace_id": "workspace-recovery",
        "task_id": "task-recovery",
        "event_type": event_type,
        "actor_type": actor_type or ("operator" if event_type == "task.started" else "engine"),
        "actor_id": actor_id or ("operator-1" if event_type == "task.started" else "content_intelligence"),
        "idempotency_key": key,
        "created_at": created_at,
        "correlation_id": "corr-attempt-1",
        "payload": payload if payload is not None else ({"attempt_id": key[:-8], "engine_id": "content_intelligence", "capability": "research.analyze"} if event_type == "task.started" else {}),
    }


def test_recovery_assessment_classifies_approved_task_as_not_started():
    result = assess_execution_recovery(
        task=_recovery_task("approved"),
        events=[],
        mission_id="mission-recovery",
        workspace_id="workspace-recovery",
        now=datetime(2026, 11, 19, 12, tzinfo=timezone.utc),
        stale_after=timedelta(minutes=5),
    )
    assert result.classification == "not_started"


def test_recovery_assessment_keeps_stale_started_attempt_outcome_unknown():
    result = assess_execution_recovery(
        task=_recovery_task("executing"),
        events=[_recovery_event("task.started", "attempt-1:started")],
        assigned_engine_id="content_intelligence",
        assigned_capability="research.analyze",
        mission_id="mission-recovery",
        workspace_id="workspace-recovery",
        now=datetime(2026, 11, 19, 12, tzinfo=timezone.utc),
        stale_after=timedelta(minutes=5),
    )
    assert result.classification == "outcome_unknown"
    assert result.attempt_key == "attempt-1"
    assert "no_authoritative_terminal_outcome" in result.reason


def test_recovery_assessment_does_not_escalate_a_fresh_execution():
    result = assess_execution_recovery(
        task=_recovery_task("executing"),
        events=[_recovery_event("task.started", "attempt-1:started", created_at="2026-11-19T11:59:00+00:00")],
        assigned_engine_id="content_intelligence",
        assigned_capability="research.analyze",
        mission_id="mission-recovery",
        workspace_id="workspace-recovery",
        now=datetime(2026, 11, 19, 12, tzinfo=timezone.utc),
        stale_after=timedelta(minutes=5),
    )
    assert result.classification == "not_stale"


def test_recovery_assessment_reconciles_matching_durable_completion():
    result = assess_execution_recovery(
        task=_recovery_task("completed"),
        events=[
            _recovery_event("task.started", "attempt-1:started"),
            _recovery_event("task.completed", "attempt-1:completed", created_at="2026-11-19T10:01:00+00:00", payload={"summary": "Done", "completed_at": "2026-11-19T10:01:00+00:00"}),
        ],
        mission_id="mission-recovery",
        workspace_id="workspace-recovery",
        assigned_engine_id="content_intelligence",
        assigned_capability="research.analyze",
        now=datetime(2026, 11, 19, 12, tzinfo=timezone.utc),
        stale_after=timedelta(minutes=5),
    )
    assert result.classification == "completed_response_lost"
    assert len(result.evidence_event_ids) == 2


def test_recovery_assessment_requires_explicit_no_side_effects_evidence():
    events = [
        _recovery_event("task.started", "attempt-1:started"),
        _recovery_event(
            "task.failed", "attempt-1:failed",
            payload={"no_side_effects_confirmed": True, "no_side_effects_evidence_ref": "engine-receipt:attempt-1"},
            actor_type="system", actor_id="director_recovery_reconciler",
        ),
    ]
    result = assess_execution_recovery(
        task=_recovery_task("failed"),
        events=events,
        assigned_engine_id="content_intelligence",
        assigned_capability="research.analyze",
        mission_id="mission-recovery",
        workspace_id="workspace-recovery",
        now=datetime(2026, 11, 19, 12, tzinfo=timezone.utc),
        stale_after=timedelta(minutes=5),
    )
    assert result.classification == "failed_reconsiderable"

    events[1]["payload"] = {"reason": "timeout"}
    result = assess_execution_recovery(
        task=_recovery_task("failed"),
        events=events,
        mission_id="mission-recovery",
        workspace_id="workspace-recovery",
        now=datetime(2026, 11, 19, 12, tzinfo=timezone.utc),
        stale_after=timedelta(minutes=5),
    )
    assert result.classification == "outcome_unknown"


def test_recovery_assessment_fails_closed_on_scope_mismatch_or_missing_start():
    mismatched = _recovery_event("task.started", "attempt-1:started")
    mismatched["workspace_id"] = "another-workspace"
    result = assess_execution_recovery(
        task=_recovery_task("executing"),
        events=[mismatched],
        mission_id="mission-recovery",
        workspace_id="workspace-recovery",
        now=datetime(2026, 11, 19, 12, tzinfo=timezone.utc),
        stale_after=timedelta(minutes=5),
    )
    assert result.classification == "outcome_unknown"
    assert result.reason == "execution_start_event_missing_or_duplicated"


@pytest.mark.asyncio
async def test_runtime_recovery_assessment_is_read_only_and_never_dispatches():
    store = MemoryMissionStore()
    store.task["status"] = "executing"
    store.task["payload"]["status"] = "executing"
    store.row["state_snapshot"]["task_status"][store.task_id] = "executing"
    store.row["state_snapshot"]["task_assignments"][store.task_id] = {
        "engine_id": "content_intelligence", "capability": "research.analyze"
    }
    store.events.append({
        "event_id": "event-start",
        "mission_id": store.mission_id,
        "scope_type": "workspace",
        "workspace_id": store.workspace_id,
        "task_id": store.task_id,
        "event_type": "task.started",
        "actor_type": "operator",
        "actor_id": "operator-1",
        "idempotency_key": "attempt-read-only:started",
        "created_at": "2026-11-19T10:00:00+00:00",
        "correlation_id": "corr-attempt-1",
        "payload": {"requested_by": "operator-1", "attempt_id": "attempt-read-only", "engine_id": "content_intelligence", "capability": "research.analyze"},
    })
    before_task = copy.deepcopy(store.task)
    before_row = copy.deepcopy(store.row)
    before_events = copy.deepcopy(store.events)
    runtime = MissionOrchestrationRuntime(store=store, adapters=AdapterRegistry())

    result = await runtime.assess_task_recovery(
        mission_id=store.mission_id,
        workspace_id=store.workspace_id,
        task_id=store.task_id,
        stale_after_seconds=300,
        now=datetime(2026, 11, 19, 12, tzinfo=timezone.utc),
    )

    assert result.classification == "outcome_unknown"
    assert result.reason == "stale_execution_has_no_authoritative_terminal_outcome_hold_and_reconcile"
    assert store.task == before_task
    assert store.row == before_row
    assert store.events == before_events


def test_recovery_assessment_rejects_terminal_event_conflicting_with_executing_task():
    result = assess_execution_recovery(
        task=_recovery_task("executing"),
        events=[
            _recovery_event("task.started", "attempt-1:started"),
            _recovery_event(
                "task.completed", "attempt-1:completed",
                payload={"summary": "Done", "completed_at": "2026-11-19T10:01:00+00:00"},
            ),
        ],
        mission_id="mission-recovery",
        workspace_id="workspace-recovery",
        assigned_engine_id="content_intelligence",
        assigned_capability="research.analyze",
        now=datetime(2026, 11, 19, 12, tzinfo=timezone.utc),
        stale_after=timedelta(minutes=5),
    )
    assert result.classification == "outcome_unknown"
    assert result.reason == "terminal_event_conflicts_with_executing_canonical_task"


def test_recovery_assessment_rejects_approved_task_with_orphan_terminal_event():
    result = assess_execution_recovery(
        task=_recovery_task("approved"),
        events=[_recovery_event("task.failed", "orphan:failed")],
        mission_id="mission-recovery",
        workspace_id="workspace-recovery",
        now=datetime(2026, 11, 19, 12, tzinfo=timezone.utc),
        stale_after=timedelta(minutes=5),
    )
    assert result.classification == "outcome_unknown"
    assert result.reason == "approved_task_has_execution_lifecycle_event"



def test_recovery_assessment_rejects_attempt_key_payload_mismatch():
    start = _recovery_event("task.started", "attempt-1:started")
    start["payload"]["attempt_id"] = "another-attempt"
    result = assess_execution_recovery(
        task=_recovery_task("executing"),
        events=[start],
        assigned_engine_id="content_intelligence",
        assigned_capability="research.analyze",
        mission_id="mission-recovery",
        workspace_id="workspace-recovery",
        now=datetime(2026, 11, 19, 12, tzinfo=timezone.utc),
        stale_after=timedelta(minutes=5),
    )
    assert result.classification == "outcome_unknown"
    assert result.reason == "execution_attempt_identity_engine_or_capability_mismatch"
