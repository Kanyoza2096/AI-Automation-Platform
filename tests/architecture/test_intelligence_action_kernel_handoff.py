import pytest

from core.request_context import RequestContext
from services.intelligence_action_intent_admission import IntelligenceActionIntentAdmissionGate
from services.intelligence_action_kernel_handoff import IntelligenceActionKernelHandoff
from services.intelligence_evidence_graph import IntelligenceEvidenceGraph
from services.intelligence_policy_governance import IntelligencePolicy, IntelligencePolicyGovernor


class FakeKernel:
    def __init__(self):
        self.calls = []

    async def enqueue(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True, "status": "pending", "intent_id": "act-core-1"}


def _graph_and_governed_intent():
    graph = IntelligenceEvidenceGraph()
    signal = graph.add_node(node_type="signal", workspace_id="ws-1", payload={"x": 1}, provenance={"source": "test"})
    interpretation = graph.add_node(node_type="interpretation", workspace_id="ws-1", payload={"x": 1}, provenance={"signal_node_id": signal.node_id})
    graph.link(signal.node_id, interpretation.node_id)
    decision = graph.add_node(node_type="decision", workspace_id="ws-1", payload={"x": 1}, provenance={"interpretation_node_id": interpretation.node_id})
    graph.link(interpretation.node_id, decision.node_id)
    recommendation = graph.add_node(
        node_type="recommendation", workspace_id="ws-1",
        payload={"action_type": "notify_operator", "risk_class": "MEDIUM", "confidence": .95, "uncertainty": 0.0,
                 "autonomy_level": "L1", "evidence_refs": ["e1"], "rationale": "test"},
        provenance={"decision_node_id": decision.node_id},
    )
    graph.link(decision.node_id, recommendation.node_id)
    policy = IntelligencePolicy(policy_id="p1", workspace_id="ws-1")
    governor = IntelligencePolicyGovernor()
    evaluation, action = governor.govern_to_action_intent(
        graph=graph, recommendation_node_id=recommendation.node_id, policy=policy,
        idempotency_key="idem-1", extra_action_fields={"parameters": {"message": "hello"}, "risk_level": "medium"},
    )
    admission = IntelligenceActionIntentAdmissionGate().admit(
        graph=graph, action_intent_node_id=action.node_id, policy=policy, evaluation=evaluation
    )
    return graph, policy, evaluation, action, admission


@pytest.mark.asyncio
async def test_admitted_intent_crosses_canonical_authorization_then_kernel():
    graph, policy, evaluation, action, admission = _graph_and_governed_intent()
    kernel = FakeKernel()
    ctx = RequestContext("user-1", "ws-1", role="operator", permissions=("write",), correlation_id="corr-1")
    handoff = await IntelligenceActionKernelHandoff(kernel=kernel).handoff(
        graph=graph, admission=admission, action_intent_node_id=action.node_id,
        policy=policy, evaluation=evaluation, request_context=ctx,
    )
    assert handoff.status == "pending"
    assert handoff.workspace_id == "ws-1"
    assert kernel.calls[0]["action_type"] == "notify_operator"
    assert kernel.calls[0]["decision_id"] == handoff.decision_id
    assert kernel.calls[0]["idempotency_key"] == "idem-1"
    assert kernel.calls[0]["source"] == IntelligenceActionKernelHandoff.SOURCE


@pytest.mark.asyncio
async def test_workspace_mismatch_is_rejected_before_kernel():
    graph, policy, evaluation, action, admission = _graph_and_governed_intent()
    kernel = FakeKernel()
    ctx = RequestContext("user-1", "ws-2", role="operator", permissions=("write",))
    with pytest.raises(PermissionError, match="workspace mismatch"):
        await IntelligenceActionKernelHandoff(kernel=kernel).handoff(
            graph=graph, admission=admission, action_intent_node_id=action.node_id,
            policy=policy, evaluation=evaluation, request_context=ctx,
        )
    assert kernel.calls == []


@pytest.mark.asyncio
async def test_forged_admission_fingerprint_is_rejected_before_kernel():
    graph, policy, evaluation, action, admission = _graph_and_governed_intent()
    forged = type(admission)(**{**admission.to_dict(), "disposition": admission.disposition, "admission_fingerprint": "forged"})
    kernel = FakeKernel()
    ctx = RequestContext("user-1", "ws-1", role="operator", permissions=("write",))
    with pytest.raises(ValueError, match="fingerprint"):
        await IntelligenceActionKernelHandoff(kernel=kernel).handoff(
            graph=graph, admission=forged, action_intent_node_id=action.node_id,
            policy=policy, evaluation=evaluation, request_context=ctx,
        )
    assert kernel.calls == []


@pytest.mark.asyncio
async def test_kernel_is_not_called_when_canonical_authorization_rejects(monkeypatch):
    graph, policy, evaluation, action, admission = _graph_and_governed_intent()
    kernel = FakeKernel()
    def blocked(*args, **kwargs):
        class R:
            allowed = False
            reason = "blocked-by-canonical-policy"
            decision_id = "dec-block"
            workspace_id = "ws-1"
        return R()
    monkeypatch.setattr("services.intelligence_action_kernel_handoff.run_action_pipeline", blocked)
    ctx = RequestContext("user-1", "ws-1", role="operator", permissions=("write",))
    with pytest.raises(PermissionError, match="rejected intelligence"):
        await IntelligenceActionKernelHandoff(kernel=kernel).handoff(
            graph=graph, admission=admission, action_intent_node_id=action.node_id,
            policy=policy, evaluation=evaluation, request_context=ctx,
        )
    assert kernel.calls == []

@pytest.mark.asyncio
async def test_uncertain_or_rejected_intelligence_never_reaches_handoff():
    graph = IntelligenceEvidenceGraph()
    signal = graph.add_node(node_type="signal", workspace_id="ws-1", payload={"x": 1}, provenance={"source": "test"})
    interpretation = graph.add_node(node_type="interpretation", workspace_id="ws-1", payload={"x": 1}, provenance={"signal_node_id": signal.node_id})
    graph.link(signal.node_id, interpretation.node_id)
    decision = graph.add_node(node_type="decision", workspace_id="ws-1", payload={}, provenance={"interpretation_node_id": interpretation.node_id})
    graph.link(interpretation.node_id, decision.node_id)
    recommendation = graph.add_node(
        node_type="recommendation", workspace_id="ws-1",
        payload={"action_type": "notify_operator", "risk_class": "MEDIUM", "confidence": .95, "uncertainty": .8,
                 "autonomy_level": "L1", "evidence_refs": ["e1"]},
        provenance={"decision_node_id": decision.node_id},
    )
    graph.link(decision.node_id, recommendation.node_id)
    policy = IntelligencePolicy(policy_id="p1", workspace_id="ws-1", max_uncertainty=0.0)
    evaluation, action = IntelligencePolicyGovernor().govern_to_action_intent(
        graph=graph, recommendation_node_id=recommendation.node_id, policy=policy, idempotency_key="idem-x"
    )
    assert action is None
    assert evaluation.disposition.value == "HOLD_UNCERTAIN"


@pytest.mark.asyncio
async def test_reapproval_required_never_reaches_kernel():
    graph, policy, evaluation, action, admission = _graph_and_governed_intent()
    approval_policy = IntelligencePolicy(
        policy_id="p-high", workspace_id="ws-1",
        min_confidence_by_risk={**policy.min_confidence_by_risk},
        approval_required_by_risk={**policy.approval_required_by_risk},
    )
    # Admission is tied to p1; using p-high must fail before the kernel.
    kernel = FakeKernel()
    ctx = RequestContext("user-1", "ws-1", role="operator", permissions=("write",))
    with pytest.raises(ValueError, match="policy"):
        await IntelligenceActionKernelHandoff(kernel=kernel).handoff(
            graph=graph, admission=admission, action_intent_node_id=action.node_id,
            policy=approval_policy, evaluation=evaluation, request_context=ctx,
        )
    assert kernel.calls == []


@pytest.mark.asyncio
async def test_intelligence_handoff_has_no_provider_or_connector_calls():
    graph, policy, evaluation, action, admission = _graph_and_governed_intent()
    kernel = FakeKernel()
    ctx = RequestContext("user-1", "ws-1", role="operator", permissions=("write",))
    handoff = await IntelligenceActionKernelHandoff(kernel=kernel).handoff(
        graph=graph, admission=admission, action_intent_node_id=action.node_id,
        policy=policy, evaluation=evaluation, request_context=ctx,
    )
    assert handoff.kernel_intent_id == "act-core-1"
    assert len(kernel.calls) == 1
    assert "provider" not in kernel.calls[0]["source"].lower()
    assert "connector" not in kernel.calls[0]["source"].lower()
    assert kernel.calls[0]["parameters"]["_kdo_intelligence"]["admission_fingerprint"] == admission.admission_fingerprint


@pytest.mark.asyncio
async def test_real_action_kernel_accepts_handoff_without_executing_provider():
    from services.action_kernel import build_default_action_kernel
    graph, policy, evaluation, action, admission = _graph_and_governed_intent()
    kernel = build_default_action_kernel(None, allow_in_memory=True)
    ctx = RequestContext("user-1", "ws-1", role="operator", permissions=("write",), correlation_id="corr-real")
    handoff = await IntelligenceActionKernelHandoff(kernel=kernel).handoff(
        graph=graph, admission=admission, action_intent_node_id=action.node_id,
        policy=policy, evaluation=evaluation, request_context=ctx,
    )
    assert handoff.status == "pending"
    row = kernel._memory_intents[handoff.kernel_intent_id]
    assert row["status"] == "pending"
    assert row["action_type"] == "notify_operator"
    assert row["idempotency_key"] == "idem-1"
    assert row["parameters"]["_kdo_intelligence"]["action_intent_node_id"] == action.node_id


@pytest.mark.asyncio
async def test_intelligence_provenance_survives_kernel_to_execution_receipt():
    from services.action_kernel import ActionContract, ActionKernel

    kernel = ActionKernel(None, allow_in_memory=True)
    kernel.register(ActionContract(
        action_type="notify_operator",
        risk_level="medium",
        required_parameters=("message",),
        handler=lambda params, intent: {"ok": True, "provider_object_id": "provider-42", "message": params["message"]},
    ))
    graph, policy, evaluation, action, admission = _graph_and_governed_intent()
    ctx = RequestContext("user-1", "ws-1", role="operator", permissions=("write",), correlation_id="corr-receipt")

    handoff = await IntelligenceActionKernelHandoff(kernel=kernel).handoff(
        graph=graph, admission=admission, action_intent_node_id=action.node_id,
        policy=policy, evaluation=evaluation, request_context=ctx,
    )
    row = kernel._memory_intents[handoff.kernel_intent_id]
    result = await kernel.process_outbox_row(row)

    assert result["status"] == "succeeded"
    execution_id = result["execution_id"]
    receipt = kernel.executor.ledger._memory[handoff.idempotency_key]
    provenance = receipt.evidence["intelligence_provenance"]
    assert provenance["action_intent_node_id"] == action.node_id
    assert provenance["admission_fingerprint"] == admission.admission_fingerprint
    assert provenance["policy_id"] == admission.policy_id
    assert provenance["recommendation_node_id"] == admission.recommendation_node_id
    assert result["output"]["receipt"]["metadata"]["intelligence_provenance"] == provenance


@pytest.mark.asyncio
async def test_intelligence_provenance_survives_uncertain_receipt_without_success_upgrade():
    from services.action_kernel import ActionContract, ActionKernel

    kernel = ActionKernel(None, allow_in_memory=True)
    kernel.register(ActionContract(
        action_type="notify_operator",
        risk_level="medium",
        required_parameters=("message",),
        handler=lambda params, intent: {"ok": True},  # no provider object ID => uncertain
    ))
    graph, policy, evaluation, action, admission = _graph_and_governed_intent()
    ctx = RequestContext("user-1", "ws-1", role="operator", permissions=("write",), correlation_id="corr-uncertain")

    handoff = await IntelligenceActionKernelHandoff(kernel=kernel).handoff(
        graph=graph, admission=admission, action_intent_node_id=action.node_id,
        policy=policy, evaluation=evaluation, request_context=ctx,
    )
    row = kernel._memory_intents[handoff.kernel_intent_id]
    result = await kernel.process_outbox_row(row)

    assert result["status"] == "uncertain"
    receipt = kernel.executor.ledger._memory[handoff.idempotency_key]
    assert receipt.status == "uncertain"
    assert receipt.evidence["intelligence_provenance"]["admission_fingerprint"] == admission.admission_fingerprint
    assert result["output"]["receipt"]["metadata"]["intelligence_provenance"]["action_intent_node_id"] == action.node_id
