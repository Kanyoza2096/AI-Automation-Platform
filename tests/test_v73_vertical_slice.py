import asyncio

from services.action_kernel import build_default_action_kernel
from services.business_decision_action_handoff import build_action_kernel_handoff, handoff_fingerprint
from services.business_decision_policy import evaluate_decision_candidate
from services.v73_intelligence_execution_convergence import IntelligenceExecutionConvergence


class FakeCRMPlugin:
    def __init__(self):
        self.calls = []

    def execute(self, command, parameters, workspace_id=None):
        self.calls.append((command, dict(parameters), workspace_id))
        return {"ok": True, "data": {"meeting_id": "meeting-001"}}


def _candidate():
    return {
        "schema_version": "1.0", "candidate_id": "dc_vertical_001",
        "understanding_id": "bu_vertical_001", "workspace_id": "ws-1",
        "customer_id": "cust-1", "message_id": "msg-vertical-1",
        "candidate_action": "crm_add_meeting", "risk_level": "medium",
        "requires_approval": False, "status": "proposed", "reason": "meeting request",
        "intent": "meeting_request", "intent_confidence": .99,
        "governance_score": .99, "governance_status": "decision_eligible",
        "resolution_status": "consensus", "disagreement": False, "constraints": [],
        "authority": "none",
    }


def test_v73_vertical_slice_is_single_external_effect_on_duplicate_delivery():
    async def run():
        evaluation = evaluate_decision_candidate(_candidate())
        handoff = build_action_kernel_handoff(evaluation)
        event = {
            "event_id": "evt-vertical-1",
            "correlation_id": "corr-vertical-1",
            "payload": {
                "business_decision_action_handoff": handoff,
                "handoff_fingerprint": handoff_fingerprint(handoff),
            },
        }

        plugin = FakeCRMPlugin()
        kernel = build_default_action_kernel(None, allow_in_memory=True, plugin=plugin)
        convergence = IntelligenceExecutionConvergence(kernel=kernel)

        first = await convergence.admit(event=event, workspace_id="ws-1")
        second = await convergence.admit(event=event, workspace_id="ws-1")

        assert first["intent_id"] == second["intent_id"]
        assert first["idempotency_key"] == second["idempotency_key"]
        assert len(kernel._memory_intents) == 1

        intent_id = first["intent_id"]
        row = dict(kernel._memory_intents[intent_id])
        first_execution = await kernel.process_outbox_row(row)
        second_execution = await kernel.process_outbox_row(row)

        assert first_execution["status"] == "succeeded"
        assert second_execution["status"] == "succeeded"
        assert second_execution["replayed"] is True
        assert len(plugin.calls) == 1
        assert kernel._memory_intents[intent_id]["status"] == "succeeded"

    asyncio.run(run())
