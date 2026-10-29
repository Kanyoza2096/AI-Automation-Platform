import asyncio
from plugins.industries.commerce.repository import CommerceRepository
from services.action_kernel import build_default_action_kernel
from services.sector_commerce_order_notification_reconciliation import CommerceOrderNotificationReconciliationService

class FakeTwilio:
    def __init__(self,status="sent"): self.status=status; self.calls=[]
    def send_sms(self,**kwargs): self.calls.append(kwargs); return {"status":"succeeded","provider_object_id":"SMCOM1"}
    def get_message(self,sid): self.calls.append(("read",sid)); return {"sid":sid,"status":self.status}

def repo():
    r=CommerceRepository(); c=r.add_customer("com-a",{"name":"Customer","phone":"+265888000222"}); p=r.add_product("com-a",{"name":"Widget","sku":"W1","price":10,"inventory_quantity":5}); o=r.create_order("com-a",c.id,[{"product_id":p.id,"quantity":1}]); return r,o,c

def test_order_notification_execution_is_uncertain_until_delivery_reconciliation():
    r,o,c=repo(); t=FakeTwilio(); k=build_default_action_kernel(allow_in_memory=True,commerce_repository=r,twilio_adapter=t)
    result=asyncio.run(k.execute(workspace_id="com-a",action_type="commerce_send_order_notification",parameters={"order_id":o.id,"message":"Your order is ready","from_":"+26580000000","reference":"order-1"},risk_level="high",decision_id="dec-com-1",source="test"))
    assert result["status"]=="uncertain" and result["reconciliation_required"] is True
    assert result["output"]["provider_receipt_is_delivery_outcome"] is False
    assert t.calls[0]["to"]==c.phone
    d=r.get_order_notification_delivery("com-a",result["output"]["delivery_id"])
    assert d.status=="uncertain" and d.provider_message_sid=="SMCOM1"

def test_order_notification_reconciliation_terminal():
    r,o,c=repo(); t=FakeTwilio("delivered")
    d=r.create_order_notification_delivery("com-a",{"order_id":o.id,"customer_id":c.id,"to_number":c.phone,"body":"Hi","idempotency_key":"k1","provider_message_sid":"SM1","status":"uncertain"})
    result=CommerceOrderNotificationReconciliationService(r,t).reconcile(workspace_id="com-a",delivery_id=d.id)
    assert result["terminal"] is True and result["status"]=="succeeded"
    assert r.get_order_notification_delivery("com-a",d.id).status=="succeeded"
    assert r.list_order_notification_reconciliation_evidence("com-a",d.id)[0].verification_state=="verified_provider_terminal"

def test_order_notification_reconciliation_nonterminal_preserves_uncertainty():
    r,o,c=repo(); t=FakeTwilio("sent")
    d=r.create_order_notification_delivery("com-a",{"order_id":o.id,"customer_id":c.id,"to_number":c.phone,"body":"Hi","idempotency_key":"k2","provider_message_sid":"SM2","status":"uncertain"})
    result=CommerceOrderNotificationReconciliationService(r,t).reconcile(workspace_id="com-a",delivery_id=d.id)
    assert result["terminal"] is False and result["status"]=="in_progress"
    assert r.get_order_notification_delivery("com-a",d.id).status=="uncertain"

def test_order_notification_missing_customer_phone_fails_closed():
    r=CommerceRepository(); c=r.add_customer("com-a",{"name":"Customer","phone":""}); p=r.add_product("com-a",{"name":"Widget","sku":"W1","price":10,"inventory_quantity":5}); o=r.create_order("com-a",c.id,[{"product_id":p.id,"quantity":1}]); k=build_default_action_kernel(allow_in_memory=True,commerce_repository=r,twilio_adapter=FakeTwilio())
    result=asyncio.run(k.execute(workspace_id="com-a",action_type="commerce_send_order_notification",parameters={"order_id":o.id,"message":"Hi","from_":"+26580000000","reference":"r1"},risk_level="high",decision_id="d",source="test"))
    assert result["status"]=="uncertain" and "customer phone" in result["output"]["error"]
