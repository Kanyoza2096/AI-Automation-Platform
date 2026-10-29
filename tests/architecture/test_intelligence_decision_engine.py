import pytest

from services.intelligence_decision_engine import (
    IntelligenceDecisionEngine,
    IntelligenceDecisionPolicy,
    LearningUseMode,
)
from services.intelligence_evidence_graph import IntelligenceEvidenceGraph
from services.intelligence_learning_decision_context import build_learning_decision_context
from services.intelligence_policy_governance import IntelligencePolicyGovernor, IntelligencePolicy, GovernanceDisposition


def _graph():
    g = IntelligenceEvidenceGraph()
    s = g.add_node(node_type="signal", workspace_id="ws-1", payload={"kind": "inventory_low"}, provenance={"source": "inventory-monitor"})
    i = g.add_node(node_type="interpretation", workspace_id="ws-1", payload={"meaning": "replenish"}, provenance={"signal_node_id": s.node_id})
    g.link(s.node_id, i.node_id, relation="interprets")
    return g, i


def _context():
    return build_learning_decision_context(
        workspace_id="ws-1",
        learning_feedback=[{
            "schema_version": "KDO.IntelligenceLearningFeedback/v1",
            "workspace_id": "ws-1", "feedback_id": "fb-1", "execution_id": "exe-1", "outcome_id": "out-1",
            "attribution_fingerprint": "attr-1", "learning_state": "positive", "measured_success": True,
            "confidence": 0.9, "evidence_fingerprint": "fp-1", "authority": "none", "causal_claim": False,
            "governance_mutation": False, "execution_authority": False,
        }],
    )


def _recommendation():
    return {
        "action_type": "supply_chain.replenish", "risk_class": "LOW", "confidence": 0.95,
        "uncertainty": 0.0, "evidence_refs": ["signal-1"], "autonomy_level": "L2",
    }


def test_learning_context_is_consumed_as_auditable_reference_only():
    g, i = _graph()
    policy = IntelligenceDecisionPolicy(policy_id="dec-pol-1", workspace_id="ws-1", learning_use=LearningUseMode.REFERENCE_ONLY)
    result = IntelligenceDecisionEngine().construct(
        graph=g, interpretation_node_id=i.node_id, policy=policy,
        decision={"decision": "replenish"}, recommendation=_recommendation(), learning_context=_context(),
    )
    assert result.recommendation.payload["confidence"] == 0.95
    assert result.recommendation.payload["risk_class"] == "LOW"
    assert result.recommendation.provenance["learning_context_id"] == _context().context_id
    assert result.recommendation.provenance["learning_advisory_only"] is True


def test_same_inputs_with_or_without_learning_keep_governance_semantics():
    engine = IntelligenceDecisionEngine()
    g1, i1 = _graph()
    g2, i2 = _graph()
    p = IntelligenceDecisionPolicy(policy_id="dec-pol-1", workspace_id="ws-1")
    a = engine.construct(graph=g1, interpretation_node_id=i1.node_id, policy=p, decision={"decision": "replenish"}, recommendation=_recommendation())
    b = engine.construct(graph=g2, interpretation_node_id=i2.node_id, policy=p, decision={"decision": "replenish"}, recommendation=_recommendation(), learning_context=_context())
    governance = IntelligencePolicy(policy_id="gov-1", workspace_id="ws-1", allowed_action_types=frozenset({"supply_chain.replenish"}))
    ea = IntelligencePolicyGovernor().evaluate(recommendation=a.recommendation, policy=governance)
    eb = IntelligencePolicyGovernor().evaluate(recommendation=b.recommendation, policy=governance)
    assert ea.disposition is eb.disposition is GovernanceDisposition.APPROVED
    assert (ea.action_type, ea.risk_class, ea.confidence, ea.uncertainty, ea.autonomy_level) == (eb.action_type, eb.risk_class, eb.confidence, eb.uncertainty, eb.autonomy_level)


def test_ignore_mode_does_not_attach_learning_context():
    g, i = _graph()
    p = IntelligenceDecisionPolicy(policy_id="dec-pol-1", workspace_id="ws-1", learning_use=LearningUseMode.IGNORE)
    result = IntelligenceDecisionEngine().construct(graph=g, interpretation_node_id=i.node_id, policy=p, decision={"decision": "replenish"}, recommendation=_recommendation(), learning_context=_context())
    assert result.recommendation.provenance["learning_context_id"] is None
    assert result.recommendation.provenance["learning_source_fingerprints"] == []


def test_learning_context_workspace_mismatch_fails_closed():
    g, i = _graph()
    bad = build_learning_decision_context(workspace_id="ws-2", learning_feedback=[])
    p = IntelligenceDecisionPolicy(policy_id="dec-pol-1", workspace_id="ws-1")
    with pytest.raises(PermissionError, match="workspace"):
        IntelligenceDecisionEngine().construct(graph=g, interpretation_node_id=i.node_id, policy=p, decision={"decision": "replenish"}, recommendation=_recommendation(), learning_context=bad)


def test_tampered_learning_cannot_override_recommendation_fields():
    g, i = _graph()
    context = _context().to_dict()
    context["action_override"] = "security.contain_asset"
    p = IntelligenceDecisionPolicy(policy_id="dec-pol-1", workspace_id="ws-1")
    with pytest.raises(ValueError, match="action_override"):
        IntelligenceDecisionEngine().construct(graph=g, interpretation_node_id=i.node_id, policy=p, decision={"decision": "replenish"}, recommendation=_recommendation(), learning_context=context)
