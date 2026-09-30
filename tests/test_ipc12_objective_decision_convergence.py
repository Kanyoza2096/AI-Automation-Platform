from types import SimpleNamespace

import pytest

from services.business_decision_candidate import build_decision_candidate
from services.business_decision_policy import evaluate_decision_candidate
from services.business_decision_action_handoff import build_action_kernel_handoff
from services.business_objective_context import (
    bind_objective_context,
    require_objective_binding,
    resolve_active_objective_context,
)


def candidate_input():
    return {
        "schema_version": "1.0", "understanding_id": "bu_12345678901234567890",
        "message_id": "m1", "workspace_id": "ws1", "customer_id": "c1",
        "channel": "whatsapp", "correlation_id": "m1", "intent": "purchase_interest",
        "intent_confidence": 0.91, "signals": ["purchase_interest"],
        "governance_status": "decision_eligible", "decision_eligible": True,
        "interpretation_risk": "low", "governance_score": 0.86,
        "resolution_status": "consensus", "disagreement": False, "authority": "none",
    }


def objective():
    return {
        "schema_version": "1.0", "objective_id": "obj_growth", "workspace_id": "ws1",
        "objective": "Increase qualified customer conversions", "state": "active",
        "envelope_id": "env_1", "objective_updated_at": "2026-09-26T04:00:00+00:00",
        "observed_at": "2026-09-26T05:00:00+00:00", "authority": "none",
        "context_fingerprint": "fp_123",
    }


def test_unbound_candidate_cannot_become_executable_handoff():
    candidate = build_decision_candidate(candidate_input())
    candidate = bind_objective_context(candidate, None)
    evaluation = evaluate_decision_candidate(candidate)
    assert evaluation["disposition"] == "REVIEW"
    assert evaluation["objective_binding_status"] == "missing"
    # The strict binding check is intentionally a no-op for non-executable
    # dispositions; the policy already downgraded this decision to REVIEW.
    require_objective_binding(evaluation)
    handoff = build_action_kernel_handoff(evaluation, require_objective_context=True)
    assert handoff["state"] == "blocked"


def test_bound_objective_survives_candidate_policy_and_handoff():
    candidate = build_decision_candidate(candidate_input())
    candidate = bind_objective_context(candidate, objective())
    evaluation = evaluate_decision_candidate(candidate)
    assert evaluation["disposition"] == "ALLOW"
    assert evaluation["objective_id"] == "obj_growth"
    handoff = build_action_kernel_handoff(evaluation, require_objective_context=True)
    assert handoff["state"] == "ready"
    assert handoff["objective_id"] == "obj_growth"
    assert handoff["authority"] == "none"


def test_cross_workspace_objective_is_rejected():
    candidate = build_decision_candidate(candidate_input())
    with pytest.raises(ValueError, match="workspace mismatch"):
        bind_objective_context(candidate, {**objective(), "workspace_id": "ws_other"})


def test_objective_tampering_is_rejected():
    candidate = build_decision_candidate(candidate_input())
    bound = bind_objective_context(candidate, objective())
    evaluation = evaluate_decision_candidate(bound)
    evaluation["objective_id"] = "obj_other"
    with pytest.raises(ValueError, match="does not match"):
        require_objective_binding(evaluation)


def test_resolver_uses_workspace_active_objective_only():
    class Table:
        def select(self, *_): return self
        def eq(self, *_): return self
        def order(self, *_args, **_kwargs): return self
        def limit(self, *_): return self
        def execute(self):
            return SimpleNamespace(data=[{
                "objective_id": "obj_growth", "workspace_id": "ws1",
                "objective": "Increase qualified customer conversions", "state": "active",
                "envelope_id": "env_1", "updated_at": "2026-09-26T04:00:00+00:00",
            }])
    class DB:
        def table(self, name):
            assert name == "business_operating_objectives"
            return Table()
    context = resolve_active_objective_context("ws1", DB())
    assert context["objective_id"] == "obj_growth"
    assert context["workspace_id"] == "ws1"
    assert context["state"] == "active"
