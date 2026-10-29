import asyncio
from types import SimpleNamespace
from plugins.industries.commerce.repository import CommerceRepository
from services.action_kernel import build_default_action_kernel

class FakeStripe:
    def __init__(self, observed=None): self.captured=[]; self.observed=observed or {"id":"pi_1","status":"succeeded"}
    def capture_payment_intent(self, ref): self.captured.append(ref); return {"status":"uncertain","provider_object_id":ref,"outcome":"uncertain"}
    def get_payment_intent(self, ref): return self.observed

def setup():
    repo=CommerceRepository(); customer=repo.add_customer("w",{"name":"C","phone":"+265888"}); product=repo.add_product("w",{"name":"P","sku":"P1","price":100,"inventory_quantity":5}); order=repo.create_order("w",customer.id,[{"product_id":product.id,"quantity":1}]); payment=repo.record_payment("w",{"order_id":order.id,"amount":100,"currency":"MWK","status":"authorized","provider_reference":"pi_1"}); return repo,order,payment

def test_capture_requires_existing_provider_reference_and_never_promotes_receipt():
    repo,order,payment=setup(); stripe=FakeStripe(); kernel=build_default_action_kernel(allow_in_memory=True, commerce_repository=repo, stripe_adapter=stripe)
    intent=SimpleNamespace(workspace_id="w")
    result=asyncio.run(kernel._contracts["commerce_capture_payment"].handler({"order_id":order.id,"amount":100,"currency":"MWK"}, intent))
    assert result["outcome"] == "uncertain" and result["capture_is_business_outcome"] is False
    assert stripe.captured == ["pi_1"]

def test_capture_reconciliation_only_terminalizes_from_authoritative_provider_state():
    repo,order,payment=setup(); stripe=FakeStripe();
    from services.sector_commerce_payment_reconciliation import CommercePaymentReconciliationService
    result=CommercePaymentReconciliationService(repo,stripe).reconcile(workspace_id="w",payment_id=payment.id)
    assert result["status"] == "succeeded" and result["terminal"] is True
    assert repo.get_payment("w",payment.id).status == "captured"

def test_capture_reconciliation_preserves_nonterminal_state():
    repo,order,payment=setup(); stripe=FakeStripe({"id":"pi_1","status":"requires_action"})
    from services.sector_commerce_payment_reconciliation import CommercePaymentReconciliationService
    result=CommercePaymentReconciliationService(repo,stripe).reconcile(workspace_id="w",payment_id=payment.id)
    assert result["status"] == "in_progress" and result["terminal"] is False
    assert repo.get_payment("w",payment.id).status == "uncertain"
