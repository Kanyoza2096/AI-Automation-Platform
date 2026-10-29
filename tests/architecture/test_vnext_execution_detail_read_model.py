from services.vnext_execution_detail_read_model import build_execution_detail_read_model, SECTIONS

def ex(status="succeeded"):
    return {"execution_id":"exe_1","workspace_id":"ws_1","action":"send_message","idempotency_key":"idem_1","status":status,"attempt":1,"provider":"example","provider_request_id":"req_1","provider_object_id":"obj_1","evidence":{},"error":None}

def test_chain_and_authority():
    r=build_execution_detail_read_model(workspace_id="ws_1",execution=ex(),intent={"intent_id":"int_1","workspace_id":"ws_1","decision_id":"dec_1","status":"succeeded","output":{"ok":True}},governance={"decision_id":"dec_1","workspace_id":"ws_1","approved":True},observations=[{"execution_id":"exe_1","workspace_id":"ws_1","observation_type":"business_outcome","business_success":True}],attributions=[{"execution_id":"exe_1","workspace_id":"ws_1","attribution_status":"attributed","authority":"none"}])
    assert tuple(r["sections"])==SECTIONS and r["authority"]["readOnly"] and not r["authority"]["actionAuthorityGranted"] and not r["authority"]["reconciliationAuthorityGranted"]
    assert r["sections"]["providerReceipt"]["data"]["providerObjectId"]=="obj_1" and r["summary"]["businessSuccess"] is True

def test_uncertain_requires_reconciliation():
    r=build_execution_detail_read_model(workspace_id="ws_1",execution={**ex("uncertain"),"provider_object_id":None,"error":"ambiguous"})
    assert r["summary"]["reconciliationRequired"] and r["summary"]["businessSuccess"] is None

def test_missing_evidence_is_unknown():
    r=build_execution_detail_read_model(workspace_id="ws_1",execution=ex())
    assert r["sections"]["intent"]["state"]=="UNKNOWN" and r["sections"]["governance"]["state"]=="UNKNOWN" and r["sections"]["verification"]["state"]=="UNKNOWN"

def test_workspace_mismatch_rejected():
    try: build_execution_detail_read_model(workspace_id="ws_2",execution=ex())
    except ValueError as e: assert "workspace mismatch" in str(e)
    else: raise AssertionError("workspace mismatch must be rejected")
