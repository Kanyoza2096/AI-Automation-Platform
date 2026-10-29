from services.intelligence_action_intent_admission import IntelligenceActionIntentAdmissionGate
from services.intelligence_evidence_graph import IntelligenceEvidenceGraph
from services.intelligence_policy_governance import (
    GovernanceDisposition,
    IntelligencePolicy,
    IntelligencePolicyGovernor,
    RiskClass,
)


def _graph():
    g = IntelligenceEvidenceGraph()
    s = g.add_node(node_type="signal", workspace_id="ws-1", payload={"kind": "inventory_low"}, provenance={"source": "inventory-monitor"})
    i = g.add_node(node_type="interpretation", workspace_id="ws-1", payload={"meaning": "replenish"}, provenance={"signal_node_id": s.node_id})
    d = g.add_node(node_type="decision", workspace_id="ws-1", payload={"decision": "replenish"}, provenance={"interpretation_node_id": i.node_id})
    r = g.add_node(
        node_type="recommendation",
        workspace_id="ws-1",
        payload={"action_type": "supply_chain.replenish", "risk_class": "LOW", "confidence": 0.95, "uncertainty": 0.0, "evidence_refs": [s.node_id], "autonomy_level": "L2"},
        provenance={"decision_node_id": d.node_id},
    )
    g.link(s.node_id, i.node_id, relation="interprets")
    g.link(i.node_id, d.node_id, relation="supports")
    g.link(d.node_id, r.node_id, relation="recommends")
    return g, r


def test_governor_path_performs_admission():
    g, r = _graph()
    p = IntelligencePolicy(policy_id="pol-1", workspace_id="ws-1", allowed_action_types=frozenset({"supply_chain.replenish"}))
    evaluation, action = IntelligencePolicyGovernor().govern_to_action_intent(graph=g, recommendation_node_id=r.node_id, policy=p, idempotency_key="idem-1")
    assert evaluation.disposition is GovernanceDisposition.APPROVED
    assert action is not None
    admitted = IntelligenceActionIntentAdmissionGate().admit(graph=g, action_intent_node_id=action.node_id, policy=p, evaluation=evaluation)
    assert admitted.admission_fingerprint == action.to_dict()["fingerprint"]


def test_high_risk_never_creates_action_intent_without_approval():
    g, r = _graph()
    # Make the recommendation high-risk while keeping confidence sufficient.
    row = next(n for n in g.snapshot()["nodes"] if n["node_id"] == r.node_id)
    # The graph is immutable; create a separate recommendation with the same lineage.
    high = g.add_node(
        node_type="recommendation", workspace_id="ws-1",
        payload={**row["payload"], "risk_class": RiskClass.HIGH.value},
        provenance=row["provenance"],
    )
    g.link(next(e["source_node_id"] for e in g.snapshot()["edges"] if e["target_node_id"] == r.node_id), high.node_id, relation="recommends")
    p = IntelligencePolicy(policy_id="pol-1", workspace_id="ws-1", allowed_action_types=frozenset({"supply_chain.replenish"}))
    evaluation, action = IntelligencePolicyGovernor().govern_to_action_intent(graph=g, recommendation_node_id=high.node_id, policy=p, idempotency_key="idem-2")
    assert evaluation.disposition is GovernanceDisposition.REQUIRES_APPROVAL
    assert action is None
