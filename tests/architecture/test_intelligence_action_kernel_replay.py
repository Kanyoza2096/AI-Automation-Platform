import json
import pytest
from core.request_context import RequestContext
from services.intelligence_action_kernel_handoff import IntelligenceActionKernelHandoff
from services.intelligence_action_kernel_replay import IntelligenceActionKernelReplayRepository
from services.intelligence_action_intent_replay import ActionIntentReplaySnapshot
from services.intelligence_action_intent_admission import IntelligenceActionIntentAdmissionGate
from services.intelligence_evidence_graph import IntelligenceEvidenceGraph
from services.intelligence_policy_governance import IntelligencePolicy, IntelligencePolicyGovernor

class FakeKernel:
    async def enqueue(self, **kwargs):
        return {"ok": True, "status": "pending", "intent_id": "act-core-151", "action_type": kwargs["action_type"], "idempotency_key": "kernel-idem" if kwargs["idempotency_key"] else "", "accepted": True}

def fixture():
    g=IntelligenceEvidenceGraph(); s=g.add_node(node_type="signal",workspace_id="ws-1",payload={"x":1},provenance={"source":"test"})
    i=g.add_node(node_type="interpretation",workspace_id="ws-1",payload={"x":1},provenance={"signal_node_id":s.node_id}); g.link(s.node_id,i.node_id)
    d=g.add_node(node_type="decision",workspace_id="ws-1",payload={"x":1},provenance={"interpretation_node_id":i.node_id}); g.link(i.node_id,d.node_id)
    r=g.add_node(node_type="recommendation",workspace_id="ws-1",payload={"action_type":"notify_operator","risk_class":"MEDIUM","confidence":.95,"uncertainty":0.0,"autonomy_level":"L1","evidence_refs":["e1"]},provenance={"decision_node_id":d.node_id}); g.link(d.node_id,r.node_id)
    p=IntelligencePolicy(policy_id="p1",workspace_id="ws-1"); ev,a=IntelligencePolicyGovernor().govern_to_action_intent(graph=g,recommendation_node_id=r.node_id,policy=p,idempotency_key="idem-151",extra_action_fields={"parameters":{"message":"hello"},"risk_level":"medium"})
    adm=IntelligenceActionIntentAdmissionGate().admit(graph=g,action_intent_node_id=a.node_id,policy=p,evaluation=ev)
    ctx=RequestContext("user-1","ws-1",role="operator",permissions=("write",),correlation_id="corr-151")
    return g,p,ev,a,adm,ctx

@pytest.mark.asyncio
async def test_capture_binds_authorization_to_kernel_admission(tmp_path):
    g,p,e,a,adm,ctx=fixture(); kernel=FakeKernel(); hand=await IntelligenceActionKernelHandoff(kernel=kernel).handoff(graph=g,admission=adm,action_intent_node_id=a.node_id,policy=p,evaluation=e,request_context=ctx)
    # Canonical authorization evidence is captured from the same admitted intent.
    from services.intelligence_authorization_replay import AuthorizationReplaySnapshot
    snap=ActionIntentReplaySnapshot("air-151","ws-1",a.node_id,a.fingerprint,"gr-151","gf-151","pf-151","df-151",adm.recommendation_node_id,adm.admission_fingerprint,a.to_dict())
    auth=AuthorizationReplaySnapshot("ar-151","ws-1",snap.action_intent_fingerprint,snap.replay_id,{"principal_id":"user-1","workspace_id":"ws-1","role":"operator","permissions":["write"],"species_privileges":False,"auth_method":"unknown"},{"decision":"allow","allowed":True,"reason":"allowed","decision_id":hand.decision_id,"correlation_id":"corr-151","workspace_id":"ws-1","guardian":{},"metabolism":{},"council":{},"director":{},"metadata":{}},"af-151")
    kr=IntelligenceActionKernelReplayRepository(tmp_path/"kr.sqlite"); out=kr.capture(authorization_snapshot=auth,action_intent_snapshot=snap,handoff=hand)
    assert out.kernel_intent_id
    replay=kr.replay(out.replay_id,authorization_snapshot=auth,action_intent_snapshot=snap)
    assert replay.kernel_intent_id==out.kernel_intent_id
    kr.close()

def test_tampered_kernel_record_rejected(tmp_path):
    import sqlite3
    # Minimal immutable records to test the cryptographic repository itself.
    from types import SimpleNamespace
    auth=SimpleNamespace(replay_id="ar-1",workspace_id="ws-1",authorization_fingerprint="af-1")
    action=SimpleNamespace(workspace_id="ws-1",action_intent_fingerprint="ai-1")
    hand=SimpleNamespace(workspace_id="ws-1",action_intent_node_id="n",action_type="notify_operator",decision_id="d",idempotency_key="i",kernel_intent_id="k")
    repo=IntelligenceActionKernelReplayRepository(tmp_path/"t.sqlite"); snap=repo.capture(authorization_snapshot=auth,action_intent_snapshot=action,handoff=hand,kernel_result={"ok":True,"status":"pending","intent_id":"k","action_type":"notify_operator","idempotency_key":"i","accepted":True})
    repo._conn.execute("UPDATE intelligence_action_kernel_replays SET kernel_result_json=? WHERE replay_id=?",(json.dumps({"ok":True,"status":"pending","intent_id":"tampered","accepted":True},sort_keys=True),snap.replay_id)); repo._conn.commit()
    with pytest.raises(ValueError,match="fingerprint"):
        repo.replay(snap.replay_id,authorization_snapshot=auth,action_intent_snapshot=action)
    repo.close()

def test_cross_workspace_rejected(tmp_path):
    from types import SimpleNamespace
    auth=SimpleNamespace(replay_id="ar-1",workspace_id="ws-1",authorization_fingerprint="af-1"); action=SimpleNamespace(workspace_id="ws-1",action_intent_fingerprint="ai-1"); hand=SimpleNamespace(workspace_id="ws-1",action_intent_node_id="n",action_type="x",decision_id="d",idempotency_key="i",kernel_intent_id="k")
    repo=IntelligenceActionKernelReplayRepository(tmp_path/"x.sqlite"); snap=repo.capture(authorization_snapshot=auth,action_intent_snapshot=action,handoff=hand,kernel_result={"status":"pending","intent_id":"k","accepted":True})
    with pytest.raises(PermissionError): repo.replay(snap.replay_id,authorization_snapshot=auth,action_intent_snapshot=SimpleNamespace(workspace_id="ws-2",action_intent_fingerprint="ai-1"))
    repo.close()
