import asyncio

from core.request_context import RequestContext, clear_request_context, set_request_context
from core.pipeline import run_action_pipeline, Decision
from core.autonomous_loop import AutonomousLoop


def setup_function():
    clear_request_context()


def teardown_function():
    clear_request_context()


def test_decision_created_event_contains_authoritative_gate_id(monkeypatch):
    events = []
    monkeypatch.setattr("core.pipeline._emit_event", lambda envelope: events.append(envelope.to_dict()))
    ctx = RequestContext(
        principal_id="op",
        workspace_id="ws-loop-1",
        role="operator",
        permissions=("read", "write"),
    )
    result = run_action_pipeline(action="publish", payload={"caption": "x"}, ctx=ctx, enqueue=False)
    created = next(e for e in events if e["event_type"] == "DecisionCreated")
    assert created["payload"]["decision_id"] == result.decision_id
    assert created["payload"]["approved"] is True


def test_strategy_execute_is_governed_before_outbox(monkeypatch):
    loop = AutonomousLoop()
    calls = []
    async def fake_publish(event_type, payload):
        calls.append((event_type, payload))
    loop._publish_event = fake_publish
    # No plan authority means an autonomous member must not execute a side effect.
    asyncio.run(loop._handle_strategy_execute({
        "action": "publish",
        "workspace_id": "ws-loop-2",
        "brand_id": "brand-1",
        "caption": "x",
    }))
    assert calls
    assert calls[-1][0] == "action.blocked"
    assert calls[-1][1]["decision"] in {Decision.SUGGEST.value, Decision.BLOCK.value, Decision.ESCALATE.value}


def test_unknown_event_is_acknowledged():
    loop = AutonomousLoop()
    marked = []
    async def fake_mark(event, success, error=None):
        marked.append((event, success, error))
    loop._mark_event_processed = fake_mark
    asyncio.run(loop._process_event({"event_id": "evt-unknown-1", "event_type": "unsupported_event", "payload": {}}))
    assert marked == [({"event_id": "evt-unknown-1", "event_type": "unsupported_event", "payload": {}}, True, None)]
