from datetime import datetime

from services.business_action_context import BusinessActionContextResolver
from services.business_action_intent_materializer import BusinessActionIntentMaterializer
from services.business_decision_action_handoff import build_action_kernel_handoff
from services.business_decision_policy import evaluate_decision_candidate


def candidate():
    return {
        "schema_version": "1.0", "candidate_id": "dc_context123456",
        "understanding_id": "bu_context123456", "workspace_id": "ws-1",
        "customer_id": "cust-1", "message_id": "msg-1",
        "candidate_action": "crm_schedule_followup", "risk_level": "medium",
        "requires_approval": False, "status": "proposed", "reason": "x",
        "intent": "purchase_interest", "intent_confidence": .95,
        "governance_score": .95, "governance_status": "decision_eligible",
        "resolution_status": "consensus", "disagreement": False,
        "constraints": [], "authority": "none",
    }


def test_default_followup_is_derived_from_business_policy_not_ai_output():
    evaluation = evaluate_decision_candidate(candidate())
    handoff = build_action_kernel_handoff(evaluation)
    result = BusinessActionIntentMaterializer.materialize(
        handoff, context_resolver=BusinessActionContextResolver()
    )
    assert result.status == "ready"
    assert result.parameters["contact"] == "cust-1"
    datetime.fromisoformat(result.parameters["date"])


def test_workspace_policy_can_supply_deterministic_followup_schedule():
    evaluation = evaluate_decision_candidate(candidate())
    handoff = build_action_kernel_handoff(evaluation)
    resolver = BusinessActionContextResolver(
        workspace_policy={"ws-1": {"follow_up_hours": 48, "contact": "crm-contact-7"}}
    )
    result = BusinessActionIntentMaterializer.materialize(handoff, context_resolver=resolver)
    assert result.status == "ready"
    assert result.parameters["contact"] == "crm-contact-7"
    assert result.parameters["date"]


def test_invalid_business_policy_fails_closed():
    evaluation = evaluate_decision_candidate(candidate())
    handoff = build_action_kernel_handoff(evaluation)
    resolver = BusinessActionContextResolver(
        workspace_policy={"ws-1": {"follow_up_hours": 0}}
    )
    result = BusinessActionIntentMaterializer.materialize(handoff, context_resolver=resolver)
    assert result.status == "needs_review"
    assert result.parameters == {}
