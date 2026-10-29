import asyncio
from plugins.industries.education.repository import EducationRepository
from services.action_kernel import build_default_action_kernel
from services.sector_education_parent_message_reconciliation import EducationParentMessageReconciliationService

class FakeTwilio:
    def __init__(self,status="sent"): self.status=status; self.calls=[]
    def send_sms(self,*,from_,to,body): self.calls.append((from_,to,body)); return {"status":"succeeded","provider_object_id":"SMEDU1"}
    def get_message(self,reference): self.calls.append(("read",reference)); return {"sid":reference,"status":self.status}

def repo():
    r=EducationRepository(); s=r.add_student("edu-a",{"full_name":"Student","guardian_name":"Guardian","guardian_phone":"+265888000001"}); return r,s

def test_education_parent_message_execution_is_uncertain_until_delivery_reconciliation():
    r,s=repo(); t=FakeTwilio(); k=build_default_action_kernel(allow_in_memory=True,education_repository=r,twilio_adapter=t)
    result=asyncio.run(k.execute(workspace_id="edu-a",action_type="education_send_parent_message",parameters={"student_id":s.id,"message":"Attendance update","from_":"+26580000000","reference":"att-1"},risk_level="high",decision_id="dec-edu-1",source="test"))
    assert result["status"]=="uncertain"; assert result["reconciliation_required"] is True; assert result["output"]["provider_receipt_is_delivery_outcome"] is False; assert t.calls==[("+26580000000","+265888000001","Attendance update")]

def test_education_parent_message_reconciliation_terminal():
    r,s=repo(); t=FakeTwilio("delivered"); d=r.create_parent_message_delivery("edu-a",{"student_id":s.id,"to_number":s.guardian_phone,"body":"Hi","idempotency_key":"k1","provider_message_sid":"SM1","status":"uncertain"})
    result=EducationParentMessageReconciliationService(r,t).reconcile(workspace_id="edu-a",delivery_id=d.id)
    assert result["terminal"] is True and result["status"]=="succeeded"; assert r.get_parent_message_delivery("edu-a",d.id).status=="succeeded"

def test_education_parent_message_reconciliation_nonterminal_preserves_uncertainty():
    r,s=repo(); t=FakeTwilio("sent"); d=r.create_parent_message_delivery("edu-a",{"student_id":s.id,"to_number":s.guardian_phone,"body":"Hi","idempotency_key":"k2","provider_message_sid":"SM2","status":"uncertain"})
    result=EducationParentMessageReconciliationService(r,t).reconcile(workspace_id="edu-a",delivery_id=d.id)
    assert result["terminal"] is False and result["status"]=="in_progress"; assert r.get_parent_message_delivery("edu-a",d.id).status=="uncertain"

def test_education_parent_message_missing_guardian_phone_fails_closed():
    r=EducationRepository(); s=r.add_student("edu-a",{"full_name":"Student","guardian_phone":""}); k=build_default_action_kernel(allow_in_memory=True,education_repository=r,twilio_adapter=FakeTwilio())
    result=asyncio.run(k.execute(workspace_id="edu-a",action_type="education_send_parent_message",parameters={"student_id":s.id,"message":"Hi","from_":"+26580000000","reference":"r1"},risk_level="high",decision_id="d",source="test"))
    assert result["status"] == "uncertain"
    assert "guardian phone" in result["output"]["error"]
