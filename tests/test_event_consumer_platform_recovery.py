from types import SimpleNamespace

from core.event_consumer import EventConsumer


def _recovery_payload(categories=None, *, status="available"):
    return {
        "trigger": "automation_job_failure_threshold",
        "failed_jobs": 9,
        "recovery_mode": "bounded_advisory_sweep",
        "automatic_retry": False,
        "publication_replay_allowed": False,
        "correlation_id": "job-recovery-test-correlation",
        "failure_intelligence": {
            "status": status,
            "categories": categories if categories is not None else {
                "provider_availability": {
                    "count": 5,
                    "job_ids": ["job-a", "job-b"],
                    "recovery_proposals": [],
                },
                "caption_quality": {
                    "count": 4,
                    "job_ids": ["job-c"],
                    "recovery_proposals": [],
                },
                "voice_compliance": {
                    "count": 3,
                    "job_ids": ["job-d"],
                    "recovery_proposals": [],
                },
                "semantic_validation": {
                    "count": 2,
                    "job_ids": ["job-e"],
                    "recovery_proposals": [],
                },
                "evidence_grounding": {
                    "count": 1,
                    "job_ids": ["job-f"],
                    "recovery_proposals": [],
                },
            },
        },
    }


def _patch_recovery_dependencies(monkeypatch):
    import services.director as director_package
    import services.director.orchestration.runtime as runtime_module
    import services.director.orchestration.store as store_module
    import services.job_stats_service as stats_module

    state = {"admissions": [], "spawn_calls": [], "execution_calls": []}
    agents_by_mission = {}

    class FakeDirectorStore:
        supabase = object()

    director = SimpleNamespace(store=FakeDirectorStore())

    async def get_director():
        return director

    class FakeMissionRuntimeStore:
        def __init__(self, director_store):
            assert director_store is director.store

        async def admit_platform_recovery_mission(self, **kwargs):
            state["admissions"].append(kwargs)
            return {
                "mission_id": kwargs["mission_id"],
                "task_ids": [task["id"] for task in kwargs["tasks"]],
                "replayed": len(state["admissions"]) > 1,
            }

    class FakeRuntime:
        def __init__(self, *, store, adapters):
            self.agents = agents_by_mission.setdefault("mission", {})
            self.adapters = adapters

        async def load_snapshot(self, *, mission_id, workspace_id):
            assert workspace_id is None
            self.agents = agents_by_mission.setdefault(mission_id, {})
            return SimpleNamespace(state_snapshot={"agents": dict(self.agents)})

        async def spawn_swarm(self, *, mission_id, workspace_id, task_ids, candidates,
                              complexity_score, idempotency_prefix, correlation_id):
            assert workspace_id is None
            assert len(candidates) <= 4
            state["spawn_calls"].append({
                "mission_id": mission_id,
                "task_ids": list(task_ids),
                "candidate_ids": [candidate.agent_id for candidate in candidates],
            })
            for candidate in candidates:
                self.agents[candidate.agent_id] = {
                    "task_id": candidate.task_id,
                    "status": "spawned",
                }
            return None, SimpleNamespace(state_snapshot={"agents": dict(self.agents)})

        async def execute_agent(self, *, mission_id, workspace_id, agent_id,
                                objective, inputs, idempotency_prefix,
                                correlation_id, timeout_seconds):
            assert workspace_id is None
            assert "Do not retry" in objective
            assert inputs["failure_category"]
            state["execution_calls"].append(agent_id)
            self.agents[agent_id]["status"] = "completed"
            return None

    class FakeJobStatsService:
        def __init__(self, *, supabase_client):
            assert supabase_client is director.store.supabase

        def get_failure_intelligence(self, limit=100):
            return {"status": "available", "categories": {}}

    monkeypatch.setattr(director_package, "get_director", get_director)
    monkeypatch.setattr(store_module, "MissionRuntimeStore", FakeMissionRuntimeStore)
    monkeypatch.setattr(runtime_module, "MissionOrchestrationRuntime", FakeRuntime)
    monkeypatch.setattr(stats_module, "JobStatsService", FakeJobStatsService)
    return state


def test_recovery_event_admits_and_executes_bounded_platform_specialists(monkeypatch):
    state = _patch_recovery_dependencies(monkeypatch)
    consumer = EventConsumer()

    accepted = consumer._handle_director_job_recovery_request(_recovery_payload())

    assert accepted is True
    assert len(state["admissions"]) == 1
    assert len(state["spawn_calls"]) == 2
    assert [len(call["candidate_ids"]) for call in state["spawn_calls"]] == [4, 1]
    assert len(state["execution_calls"]) == 5
    assert len(set(state["execution_calls"])) == 5
    assert all(call["mission_id"] for call in state["spawn_calls"])


def test_recovery_event_redelivery_does_not_duplicate_completed_specialists(monkeypatch):
    state = _patch_recovery_dependencies(monkeypatch)
    consumer = EventConsumer()
    payload = _recovery_payload()

    assert consumer._handle_director_job_recovery_request(payload) is True
    first_execution_count = len(state["execution_calls"])
    first_spawn_count = len(state["spawn_calls"])

    assert consumer._handle_director_job_recovery_request(payload) is True
    assert len(state["execution_calls"]) == first_execution_count
    assert len(state["spawn_calls"]) == first_spawn_count
    assert len(state["admissions"]) == 2
    assert state["admissions"][0]["idempotency_key"] == state["admissions"][1]["idempotency_key"]
    assert state["admissions"][0]["request_fingerprint"] == state["admissions"][1]["request_fingerprint"]


def test_recovery_event_rejects_unrecognized_failure_categories(monkeypatch):
    state = _patch_recovery_dependencies(monkeypatch)
    consumer = EventConsumer()

    accepted = consumer._handle_director_job_recovery_request(
        _recovery_payload({"attacker_supplied_category": {"count": 99}})
    )

    assert accepted is True
    assert state["admissions"] == []
    assert state["spawn_calls"] == []
    assert state["execution_calls"] == []


def test_recovery_event_defers_when_failure_intelligence_is_unavailable(monkeypatch):
    state = _patch_recovery_dependencies(monkeypatch)
    consumer = EventConsumer()

    accepted = consumer._handle_director_job_recovery_request(
        _recovery_payload(status="unavailable")
    )

    assert accepted is False
    assert state["admissions"] == []
    assert state["spawn_calls"] == []
