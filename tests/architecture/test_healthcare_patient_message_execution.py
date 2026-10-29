import asyncio
from services.action_kernel import build_default_action_kernel
from plugins.industries.healthcare.repository import HealthcareRepository
from services.sector_healthcare_patient_message_reconciliation import HealthcarePatientMessageReconciliationService

class FakeTwilio:
    def __init__(self,status="sent"): self.status=status; self.calls=[]
    def send_sms(self,**kwargs): self.calls.append(kwargs); return {"status":"succeeded","provider_object_id":"SMHC1","sid":"SMHC1"}
    def get_message(self,sid): return {"sid":sid,"status":self.status}

def repo():
    r=HealthcareRepository(); p=r.add_patient("hc-a",{"full_name":"Patient","phone":"+265888000111"}); return r,p

def test_patient_message_execution_is_uncertain_until_provider_delivery_reconciliation():
    r,p=repo(); t=FakeTwilio(); k=build_default_action_kernel(allow_in_memory=True,healthcare_repository=r,twilio_adapter=t)
    result=asyncio.run(k.execute(workspace_id="hc-a",action_type="healthcare_send_patient_message",parameters={"patient_id":p.id,"message":"Appointment reminder","from_":"+265888000999","reference":"appt-1"},risk_level="high",decision_id="dec-hc-1",source="test"))
    assert result["status"] == "uncertain"; assert result["reconciliation_required"] is True
    assert t.calls and t.calls[0]["to"] == p.phone
    delivery=r.get_patient_message_delivery("hc-a",result["output"]["delivery_id"])
    assert delivery.status == "uncertain" and delivery.provider_message_sid == "SMHC1"

def test_patient_message_reconciliation_promotes_only_authoritative_terminal_state():
    r,p=repo(); t=FakeTwilio("delivered")
    d=r.create_patient_message_delivery("hc-a",{"patient_id":p.id,"to_number":p.phone,"body":"Hi","idempotency_key":"k1","provider_message_sid":"SMHC1","status":"uncertain"})
    result=HealthcarePatientMessageReconciliationService(r,t).reconcile(workspace_id="hc-a",delivery_id=d.id)
    assert result["terminal"] is True and result["status"] == "succeeded"
    assert r.get_patient_message_delivery("hc-a",d.id).status == "succeeded"
    assert r.list_patient_message_reconciliation_evidence("hc-a",d.id)[0].verification_state == "verified_provider_terminal"

def test_patient_message_reconciliation_preserves_nonterminal_state():
    r,p=repo(); t=FakeTwilio("sent")
    d=r.create_patient_message_delivery("hc-a",{"patient_id":p.id,"to_number":p.phone,"body":"Hi","idempotency_key":"k2","provider_message_sid":"SMHC2","status":"uncertain"})
    result=HealthcarePatientMessageReconciliationService(r,t).reconcile(workspace_id="hc-a",delivery_id=d.id)
    assert result["terminal"] is False and result["status"] == "in_progress"
    assert r.get_patient_message_delivery("hc-a",d.id).status == "uncertain"

def test_patient_message_reconciliation_fails_closed_without_provider_reference():
    r,p=repo(); t=FakeTwilio()
    d=r.create_patient_message_delivery("hc-a",{"patient_id":p.id,"to_number":p.phone,"body":"Hi","idempotency_key":"k3","status":"uncertain"})
    result=HealthcarePatientMessageReconciliationService(r,t).reconcile(workspace_id="hc-a",delivery_id=d.id)
    assert result["status"] == "operator_review" and result["reconciliation_required"] is True
    assert not t.calls
