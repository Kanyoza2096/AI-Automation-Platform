import asyncio

from services.business_action_intent_materializer import BusinessActionIntentMaterializer
from services.business_decision_action_handoff import build_action_kernel_handoff, handoff_fingerprint
from services.business_decision_policy import evaluate_decision_candidate
from services.v73_intelligence_execution_convergence import IntelligenceExecutionConvergence


def candidate(intent, action, *, customer_id="cust-1"):
    return {
        "schema_version": "1.0", "candidate_id": "dc_abcdef123456",
        "understanding_id": "bu_abcdef123456", "workspace_id": "ws-1",
        "customer_id": customer_id, "message_id": "msg-1",
        "candidate_action": action, "risk_level": "medium",
        "requires_approval": False, "status": "proposed", "reason": "x",
        "intent": intent, "intent_confidence": .95, "governance_score": .95,
        "governance_status": "decision_eligible", "resolution_status": "consensus",
        "disagreement": False, "constraints": [], "authority": "none",
    }


def test_meeting_handoff_materializes_only_trusted_fields():
    evaluation = evaluate_decision_candidate(candidate("meeting_request", "crm_add_meeting"))
    handoff = build_action_kernel_handoff(evaluation)
    result = BusinessActionIntentMaterializer.materialize(handoff)
    assert result.status == "ready"
    assert result.parameters["title"] == "Customer-requested sales meeting"
    assert "raw_text" not in result.parameters
    assert "instructions" not in result.parameters


def test_followup_without_date_fails_closed():
    evaluation = evaluate_decision_candidate(candidate("purchase_interest", "crm_schedule_followup"))
    handoff = build_action_kernel_handoff(evaluation)
    result = BusinessActionIntentMaterializer.materialize(handoff)
    assert result.status == "needs_review"
    assert "invent scheduling data" in result.reason


def test_convergence_reuses_same_idempotency_key_for_duplicate_delivery():
    class FakeKernel:
        def __init__(self): self.calls = []
        async def enqueue(self, **kwargs):
            self.calls.append(kwargs)
            return {"ok": True, "status": "pending", "intent_id": "act-1"}

    async def run():
        evaluation = evaluate_decision_candidate(candidate("meeting_request", "crm_add_meeting"))
        handoff = build_action_kernel_handoff(evaluation)
        event = {
            "event_id": "evt-1", "correlation_id": "msg-1", "payload": {
                "business_decision_action_handoff": handoff,
                "handoff_fingerprint": handoff_fingerprint(handoff),
            }
        }
        kernel = FakeKernel()
        convergence = IntelligenceExecutionConvergence(kernel=kernel)
        first = await convergence.admit(event=event, workspace_id="ws-1")
        second = await convergence.admit(event=event, workspace_id="ws-1")
        assert first["status"] == "pending"
        assert second["status"] == "pending"
        # Delivery can replay, but the Action Kernel receives the same durable
        # identity. The kernel is the authoritative duplicate fence.
        assert len(kernel.calls) == 2
        assert kernel.calls[0]["idempotency_key"] == kernel.calls[1]["idempotency_key"]
        assert kernel.calls[0]["action_type"] == "crm_add_meeting"

    asyncio.run(run())


def test_policy_approval_required_never_becomes_autonomous_kernel_execution():
    evaluation = evaluate_decision_candidate(candidate("meeting_request", "crm_add_meeting"))
    evaluation["disposition"] = "APPROVAL_REQUIRED"
    evaluation["approval_required"] = True
    evaluation["reason"] = "policy_requires_human_approval"
    handoff = build_action_kernel_handoff(evaluation)

    class FakeKernel:
        async def enqueue(self, **kwargs):
            raise AssertionError("approval-required handoff must not reach Action Kernel enqueue")

    async def run():
        convergence = IntelligenceExecutionConvergence(kernel=FakeKernel())
        result = await convergence.admit(
            event={"event_id": "evt-approval", "payload": {
                "business_decision_action_handoff": handoff,
                "handoff_fingerprint": handoff_fingerprint(handoff),
            }},
            workspace_id="ws-1",
        )
        assert result["status"] == "approval_required"
        assert convergence.needs_review == 1

    asyncio.run(run())


def test_production_router_routes_handoff_to_v73_not_business_automation():
    class FakeContainer:
        def try_resolve(self, _type):
            raise AssertionError("handoff must not resolve BusinessAutomationEngine")

    class FakeConvergence:
        async def admit(self, *, event, workspace_id):
            return {"status": "pending", "workspace_id": workspace_id}

    async def run():
        import services.v71_14_production_route_event_wiring as wiring_mod
        original = wiring_mod.intelligence_execution_convergence if hasattr(wiring_mod, "intelligence_execution_convergence") else None
        # The router imports the singleton lazily; install a temporary module attribute
        # so the test can prove the routing decision without touching a provider.
        import services.v73_intelligence_execution_convergence as convergence_mod
        old = convergence_mod.intelligence_execution_convergence
        convergence_mod.intelligence_execution_convergence = FakeConvergence()
        try:
            wiring = wiring_mod.ProductionRouteEventWiring(container=FakeContainer(), runtime=object())
            result = await wiring.admit_event(
                event_name="business.decision.action.handoff.ready",
                event={"event_id": "evt-1", "workspace_id": "ws-1", "payload": {}},
                workspace_id="ws-1",
            )
            assert result == [{"status": "pending", "workspace_id": "ws-1"}]
            assert wiring.status()["events_admitted"] == 1
        finally:
            convergence_mod.intelligence_execution_convergence = old

    asyncio.run(run())
