import asyncio
from services.action_kernel import build_default_action_kernel
from plugins.industries.government.repository import GovernmentRepository
from services.government_applicant_notification_repository import GovernmentApplicantNotificationRepository
from services.sector_government_applicant_notification_reconciliation import GovernmentApplicantNotificationReconciliationService

class FakeTwilio:
    def __init__(self,status='sent'): self.status=status; self.calls=[]
    def send_sms(self,**kwargs): self.calls.append(kwargs); return {'status':'succeeded','provider_object_id':'SMGOV1'}
    def get_message(self,sid): return {'sid':sid,'status':self.status}

def setup():
    g=GovernmentRepository(); svc=g.create_service('gov-ws',{'name':'Permits','category':'permits'}); permit=g.create_permit('gov-ws',{'service_id':svc.id,'applicant_ref':'APP-42','permit_type':'building'}); n=GovernmentApplicantNotificationRepository(); return g,permit,n

def test_government_notification_execution_is_uncertain_until_delivery_verification():
    g,permit,n=setup(); t=FakeTwilio(); k=build_default_action_kernel(allow_in_memory=True,government_repository=g,government_notification_repository=n,twilio_adapter=t)
    r=asyncio.run(k.execute(workspace_id='gov-ws',action_type='government_notify_applicant',parameters={'permit_case_id':permit.id,'applicant_ref':'APP-42','to_number':'+265888000111','message':'Permit update','from_':'+265888000999','reference':'case-1'},risk_level='high',decision_id='d-gov-1',source='test'))
    assert r['status']=='uncertain' and r['reconciliation_required'] is True and t.calls

def test_government_notification_reconciliation_requires_terminal_provider_state():
    g,permit,n=setup(); t=FakeTwilio('delivered'); d=n.create('gov-ws',{'permit_case_id':permit.id,'applicant_ref':'APP-42','to_number':'+265888000111','body':'Hi','idempotency_key':'k','provider_message_sid':'SMGOV2','status':'uncertain'})
    r=GovernmentApplicantNotificationReconciliationService(n,t).reconcile(workspace_id='gov-ws',notification_id=d.id)
    assert r['terminal'] is True and r['status']=='succeeded'

def test_government_notification_fails_closed_on_applicant_mismatch():
    g,permit,n=setup(); t=FakeTwilio(); k=build_default_action_kernel(allow_in_memory=True,government_repository=g,government_notification_repository=n,twilio_adapter=t)
    result = asyncio.run(k.execute(workspace_id='gov-ws',action_type='government_notify_applicant',parameters={'permit_case_id':permit.id,'applicant_ref':'WRONG','to_number':'+265888000111','message':'x','from_':'+265888000999','reference':'case-1'},risk_level='high',decision_id='d-gov-2',source='test'))
    assert result.get('ok') is False and not t.calls
