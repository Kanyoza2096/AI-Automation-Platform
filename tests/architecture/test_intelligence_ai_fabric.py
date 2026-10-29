import tempfile
from pathlib import Path
import pytest

from services.ai_provider import AIReceipt
from services.intelligence_ai_fabric import AIReceiptRepository, ingest_ai_receipt_as_interpretation, receipt_fingerprint
from services.intelligence_evidence_graph import IntelligenceEvidenceGraph


def _receipt(status="succeeded", structured=None):
    return AIReceipt(status=status, provider="cloudflare_ai", model="m", raw_output="x" if status == "succeeded" else "", structured_output=structured, usage=None, correlation_id="c1", request_fingerprint="req1", error=None if status == "succeeded" else "timeout")


def test_receipt_fingerprint_is_deterministic():
    assert receipt_fingerprint(_receipt(structured={"x": 1})) == receipt_fingerprint(_receipt(structured={"x": 1}))


def test_success_receipt_becomes_interpretation_with_provider_provenance():
    g = IntelligenceEvidenceGraph(); signal = g.add_node(node_type="signal", workspace_id="ws", payload={"x": 1}, provenance={"source":"test"})
    repo = AIReceiptRepository()
    evidence, node = ingest_ai_receipt_as_interpretation(graph=g, repository=repo, receipt=_receipt(structured={"recommended_action":"x"}), workspace_id="ws", signal_node_id=signal.node_id)
    assert node.node_type == "interpretation"
    assert node.provenance["ai_receipt_fingerprint"] == evidence.receipt_fingerprint
    assert node.provenance["ai_authority"] == "none"


def test_failed_receipt_never_becomes_interpretation():
    g = IntelligenceEvidenceGraph(); signal = g.add_node(node_type="signal", workspace_id="ws", payload={}, provenance={"source":"test"})
    repo = AIReceiptRepository()
    _, node = ingest_ai_receipt_as_interpretation(graph=g, repository=repo, receipt=_receipt(status="failed"), workspace_id="ws", signal_node_id=signal.node_id)
    assert node is None


def test_uncertain_receipt_never_becomes_interpretation():
    g = IntelligenceEvidenceGraph(); signal = g.add_node(node_type="signal", workspace_id="ws", payload={}, provenance={"source":"test"})
    repo = AIReceiptRepository()
    _, node = ingest_ai_receipt_as_interpretation(graph=g, repository=repo, receipt=_receipt(status="uncertain"), workspace_id="ws", signal_node_id=signal.node_id)
    assert node is None


def test_cross_workspace_signal_rejected():
    g = IntelligenceEvidenceGraph(); signal = g.add_node(node_type="signal", workspace_id="other", payload={}, provenance={"source":"test"})
    with pytest.raises(PermissionError):
        ingest_ai_receipt_as_interpretation(graph=g, repository=AIReceiptRepository(), receipt=_receipt(structured={"x":1}), workspace_id="ws", signal_node_id=signal.node_id)


def test_receipt_repository_is_idempotent_and_restart_safe(tmp_path):
    path = Path(tmp_path) / "ai.sqlite"
    r = _receipt(structured={"x": 1})
    repo = AIReceiptRepository(path); e1 = repo.append(r, workspace_id="ws"); repo.close()
    repo2 = AIReceiptRepository(path); e2 = repo2.append(r, workspace_id="ws")
    assert e1.receipt_fingerprint == e2.receipt_fingerprint
    assert repo2.get(e1.receipt_fingerprint, workspace_id="ws").provider == "cloudflare_ai"
    repo2.close()


def test_receipt_cannot_be_read_cross_workspace(tmp_path):
    path = Path(tmp_path) / "ai.sqlite"; repo = AIReceiptRepository(path)
    e = repo.append(_receipt(structured={"x":1}), workspace_id="ws1")
    with pytest.raises(PermissionError): repo.get(e.receipt_fingerprint, workspace_id="ws2")
    repo.close()


def test_interpretation_cannot_claim_ai_authority():
    g = IntelligenceEvidenceGraph(); signal = g.add_node(node_type="signal", workspace_id="ws", payload={}, provenance={"source":"test"})
    _, node = ingest_ai_receipt_as_interpretation(graph=g, repository=AIReceiptRepository(), receipt=_receipt(structured={"x":1}), workspace_id="ws", signal_node_id=signal.node_id)
    assert node.provenance["ai_can_authorize"] is False
    assert node.provenance["ai_can_execute"] is False


def test_ai_output_does_not_become_action_intent():
    g = IntelligenceEvidenceGraph(); signal = g.add_node(node_type="signal", workspace_id="ws", payload={}, provenance={"source":"test"})
    _, node = ingest_ai_receipt_as_interpretation(graph=g, repository=AIReceiptRepository(), receipt=_receipt(structured={"recommended_action":"destroy_everything"}), workspace_id="ws", signal_node_id=signal.node_id)
    assert node.node_type == "interpretation"
    assert node.payload["ai_output"]["recommended_action"] == "destroy_everything"


def test_ai_receipt_is_not_execution_authority_import():
    import services.intelligence_ai_fabric as m
    assert not hasattr(m, "ActionKernel")
