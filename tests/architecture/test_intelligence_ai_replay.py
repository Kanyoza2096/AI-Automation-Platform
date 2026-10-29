from pathlib import Path
import sqlite3
import pytest

from services.ai_provider import AIReceipt
from services.intelligence_ai_fabric import AIReceiptRepository, AIReceiptReplayVerifier, ingest_ai_receipt_as_interpretation
from services.intelligence_decision_engine import IntelligenceDecisionEngine, IntelligenceDecisionPolicy
from services.intelligence_decision_replay import IntelligenceDecisionReplayRepository
from services.intelligence_evidence_graph import IntelligenceEvidenceGraph


def receipt(model="model-v1", structured=None):
    return AIReceipt(status="succeeded", provider="cloudflare_ai", model=model, raw_output="ok", structured_output=structured or {"meaning":"replenish"}, usage=None, correlation_id="corr-1", request_fingerprint="req-1")


def graph_with_ai(repo, model="model-v1"):
    g=IntelligenceEvidenceGraph()
    s=g.add_node(node_type="signal", workspace_id="ws", payload={"x":1}, provenance={"source":"test"})
    _, i=ingest_ai_receipt_as_interpretation(graph=g, repository=repo, receipt=receipt(model=model), workspace_id="ws", signal_node_id=s.node_id)
    return g, i


def rec():
    return {"action_type":"supply_chain.replenish","risk_class":"LOW","confidence":.95,"uncertainty":0.0,"evidence_refs":["x"],"autonomy_level":"L2"}


def test_interpretation_replay_verifies_exact_receipt(tmp_path):
    repo=AIReceiptRepository(Path(tmp_path)/"ai.sqlite"); g,i=graph_with_ai(repo)
    evidence=AIReceiptReplayVerifier(repo).verify_interpretation(graph=g, interpretation_node_id=i.node_id, workspace_id="ws")
    assert evidence.model == "model-v1"
    repo.close()


def test_provider_model_change_does_not_change_historical_receipt_identity(tmp_path):
    ai=AIReceiptRepository(Path(tmp_path)/"ai.sqlite"); g,i=graph_with_ai(ai, "model-v1")
    p=IntelligenceDecisionPolicy(policy_id="p", workspace_id="ws")
    result=IntelligenceDecisionEngine().construct(graph=g, interpretation_node_id=i.node_id, policy=p, decision={"decision":"replenish"}, recommendation=rec())
    replay=IntelligenceDecisionReplayRepository(Path(tmp_path)/"replay.sqlite")
    snap=replay.capture(graph=g,result=result)
    assert snap.ai_receipt_fingerprint == i.provenance["ai_receipt_fingerprint"]
    # A later provider/model configuration is irrelevant unless the original receipt identity changes.
    replayed=replay.replay(snap.replay_id, ai_receipt_repository=ai)
    assert replayed.ai_provider == "cloudflare_ai"
    assert replayed.ai_model == "model-v1"
    ai.close(); replay.close()


def test_tampered_durable_receipt_is_rejected(tmp_path):
    path=Path(tmp_path)/"ai.sqlite"; repo=AIReceiptRepository(path); fp=repo.append(receipt(),workspace_id="ws").receipt_fingerprint; repo.close()
    conn=sqlite3.connect(path); conn.execute("UPDATE ai_receipts SET model='model-tampered' WHERE receipt_fingerprint=?",(fp,)); conn.commit(); conn.close()
    reopened=AIReceiptRepository(path)
    with pytest.raises(ValueError, match="fingerprint mismatch|metadata mismatch"):
        reopened.get_receipt(fp, workspace_id="ws")
    reopened.close()


def test_cross_workspace_ai_replay_is_rejected(tmp_path):
    ai=AIReceiptRepository(Path(tmp_path)/"ai.sqlite"); g,i=graph_with_ai(ai)
    verifier=AIReceiptReplayVerifier(ai)
    with pytest.raises(PermissionError):
        verifier.verify_interpretation(graph=g, interpretation_node_id=i.node_id, workspace_id="other")
    ai.close()


def test_interpretation_ai_provenance_tamper_is_rejected(tmp_path):
    ai=AIReceiptRepository(Path(tmp_path)/"ai.sqlite"); g,i=graph_with_ai(ai)
    object.__setattr__(i, "provenance", {**i.provenance, "ai_model":"different-model"})
    with pytest.raises(ValueError, match="provenance mismatch"):
        AIReceiptReplayVerifier(ai).verify_interpretation(graph=g, interpretation_node_id=i.node_id, workspace_id="ws")
    ai.close()


def test_historical_replay_rejects_original_receipt_that_is_no_longer_successful(tmp_path):
    ai=AIReceiptRepository(Path(tmp_path)/"ai.sqlite"); g,i=graph_with_ai(ai)
    p=IntelligenceDecisionPolicy(policy_id="p", workspace_id="ws")
    result=IntelligenceDecisionEngine().construct(graph=g, interpretation_node_id=i.node_id, policy=p, decision={"decision":"replenish"}, recommendation=rec())
    replay=IntelligenceDecisionReplayRepository(Path(tmp_path)/"replay.sqlite"); snap=replay.capture(graph=g,result=result)
    # Repository is append-only; replacing the row simulates corrupted/legacy state and must fail verification.
    conn=sqlite3.connect(Path(tmp_path)/"ai.sqlite"); conn.execute("UPDATE ai_receipts SET status='uncertain' WHERE receipt_fingerprint=?",(snap.ai_receipt_fingerprint,)); conn.commit(); conn.close(); ai.close()
    ai=AIReceiptRepository(Path(tmp_path)/"ai.sqlite")
    with pytest.raises(ValueError, match="fingerprint mismatch|metadata mismatch|requires the original successful"):
        replay.replay(snap.replay_id, ai_receipt_repository=ai)
    ai.close(); replay.close()


def test_interpretation_semantic_replay_reconstructs_exact_projection(tmp_path):
    ai=AIReceiptRepository(Path(tmp_path)/"ai.sqlite"); g,i=graph_with_ai(ai)
    evidence=AIReceiptReplayVerifier(ai).verify_interpretation(graph=g, interpretation_node_id=i.node_id, workspace_id="ws")
    assert i.provenance["ai_interpretation_semantic_fingerprint"]
    assert evidence.receipt_fingerprint == i.provenance["ai_receipt_fingerprint"]
    ai.close()


def test_interpretation_payload_tamper_is_rejected_even_when_receipt_is_valid(tmp_path):
    ai=AIReceiptRepository(Path(tmp_path)/"ai.sqlite"); g,i=graph_with_ai(ai)
    object.__setattr__(i, "payload", {**i.payload, "ai_output": {"meaning":"tampered"}})
    with pytest.raises(ValueError, match="semantic payload mismatch"):
        AIReceiptReplayVerifier(ai).verify_interpretation(graph=g, interpretation_node_id=i.node_id, workspace_id="ws")
    ai.close()


def test_decision_replay_rejects_semantic_interpretation_substitution(tmp_path):
    ai=AIReceiptRepository(Path(tmp_path)/"ai.sqlite"); g,i=graph_with_ai(ai)
    p=IntelligenceDecisionPolicy(policy_id="p", workspace_id="ws")
    result=IntelligenceDecisionEngine().construct(graph=g, interpretation_node_id=i.node_id, policy=p, decision={"decision":"replenish"}, recommendation=rec())
    replay=IntelligenceDecisionReplayRepository(Path(tmp_path)/"replay.sqlite")
    snap=replay.capture(graph=g,result=result)
    conn=sqlite3.connect(Path(tmp_path)/"replay.sqlite")
    conn.execute("UPDATE intelligence_decision_replays SET ai_interpretation_semantic_fingerprint='tampered' WHERE replay_id=?",(snap.replay_id,)); conn.commit(); conn.close()
    with pytest.raises(ValueError, match="interpretation semantic mismatch"):
        replay.replay(snap.replay_id, ai_receipt_repository=ai)
    ai.close(); replay.close()
