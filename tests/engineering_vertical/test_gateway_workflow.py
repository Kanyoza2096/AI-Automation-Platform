from types import SimpleNamespace
from services.kdo_engineering_gateway_workflow import run_gateway_team
from services.kdo_engineering_team_events import TeamEventStore


class FakeGateway:
    def complete_structured(self, prompt, **kwargs):
        # Deliberately claim test/review outcomes: the adapter must retain these
        # only as unverified model claims, never as authoritative evidence.
        role = "planner" if "Role: planner" in prompt else "implementer" if "Role: implementer" in prompt else "test_engineer" if "Role: test_engineer" in prompt else "reviewer"
        evidence = {"tests_passed": True} if role == "test_engineer" else {"review_status": "approved", "review_id": "model-made-up"} if role == "reviewer" else {}
        return SimpleNamespace(ok=True, data={"status":"succeeded", "summary":f"{role} finished", "evidence":evidence},
            request_id=f"req-{role}", provider="fake", model="fake", finish_reason="stop")


def test_gateway_team_persists_scoped_events_but_never_authorizes_release():
    store = TeamEventStore()
    report = run_gateway_team(task={"task_id":"t1", "tenant_id":"tenant1", "workspace_id":"w1"},
        objective="Implement a small feature", acceptance_criteria=("tests pass",),
        gateway=FakeGateway(), event_store=store)
    assert report.release_authorized is False
    assert report.status == "blocked"  # model claims are not execution/review receipts
    events = store.list_events(task_id="t1", tenant_id="tenant1", workspace_id="w1")
    assert events[0]["event_type"] == "team_run_started"
    assert events[-1]["event_type"] == "team_run_finished"
    assert store.verify_chain(task_id="t1", tenant_id="tenant1", workspace_id="w1")
    assert sum(e["event_type"] == "agent_assignment" for e in events) == 4


def test_gateway_team_requires_tenant_workspace_scope():
    import pytest
    from services.kdo_engineering_gateway_workflow import GatewayWorkflowError
    with pytest.raises(GatewayWorkflowError, match="required_task_scope_missing"):
        run_gateway_team(task={"task_id":"t1", "tenant_id":"tenant1"}, objective="x",
            acceptance_criteria=("y",), gateway=FakeGateway(), event_store=TeamEventStore())
