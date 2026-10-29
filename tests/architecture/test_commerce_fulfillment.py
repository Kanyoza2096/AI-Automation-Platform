import asyncio
import httpx
from types import SimpleNamespace

from services.action_kernel import ActionKernel
from services.connectors.modern_adapters import ShopifyAdapter
from services.sector_commerce_fulfillment_execution import register_commerce_fulfill_order_action
from services.sector_commerce_fulfillment_reconciliation import CommerceFulfillmentReconciliationService


def _adapter(responses):
    def handler(request):
        payload = responses.pop(0)
        return httpx.Response(200, json=payload, request=request)
    return ShopifyAdapter(token="test-token", shop_domain="example.myshopify.com", base_url="http://testserver/admin/api/2026-07", client=httpx.Client(transport=httpx.MockTransport(handler)), allow_mutations=True, allow_high_risk=True)


def test_shopify_fulfillment_receipt_is_uncertain_until_reconciled():
    adapter = _adapter([
        {"data":{"order":{"fulfillmentOrders":{"nodes":[{"id":"gid://shopify/FulfillmentOrder/1","status":"OPEN"}]}}}},
        {"data":{"fulfillmentCreate":{"fulfillment":{"id":"gid://shopify/Fulfillment/9","status":"SUCCESS","displayStatus":"FULFILLED"},"userErrors":[]}}},
    ])
    receipt = adapter.create_fulfillment("gid://shopify/Order/1", confirm=True)
    assert receipt["status"] == "uncertain"
    assert receipt["observed_object_id"] == "gid://shopify/Fulfillment/9"
    assert receipt["metadata"]["reconciliation_required"] is True


def test_commerce_fulfillment_reconciliation_records_terminal_evidence():
    class Repo:
        def __init__(self): self.evidence=[]; self.fulfillments=[]; self.order=SimpleNamespace(id="o1", workspace_id="w1", fulfillment_status="unfulfilled")
        def get_order(self, ws, oid): return self.order if ws == "w1" and oid == "o1" else None
        def list_fulfillment_reconciliation_evidence(self, ws, oid, limit=1): return self.evidence[:limit][::-1]
        def record_fulfillment(self, ws, data): self.fulfillments.append(data); return data
        def record_fulfillment_reconciliation_evidence(self, ws, data): self.evidence.append(SimpleNamespace(**data)); return self.evidence[-1]
    class Shopify:
        def reconcile_fulfillment(self, ref): return {"status":"succeeded","provider_status":"SUCCESS","display_status":"FULFILLED","observed":{"id":ref}}
    repo=Repo(); result=CommerceFulfillmentReconciliationService(repo, Shopify()).reconcile(workspace_id="w1", order_id="o1", fulfillment_id="gid://shopify/Fulfillment/9")
    assert result["terminal"] is True
    assert repo.fulfillments[0]["status"] == "shipped"
    assert repo.evidence[0].verification_state == "verified_provider_terminal"


def test_commerce_fulfill_action_never_promotes_provider_receipt_to_business_success():
    class Repo:
        def get_order(self, ws, oid): return SimpleNamespace(id=oid, fulfillment_status="unfulfilled")
    class Shopify:
        def create_fulfillment(self, *args, **kwargs): return {"status":"uncertain","provider_object_id":"f9","metadata":{"reconciliation_required":True}}
    kernel=ActionKernel(); register_commerce_fulfill_order_action(kernel, Repo(), Shopify())
    intent=SimpleNamespace(workspace_id="w1")
    result=asyncio.run(kernel._contracts["commerce_fulfill_order"].handler({"order_id":"o1"}, intent))
    assert result["outcome"] == "uncertain"
    assert result["fulfillment_is_business_outcome"] is False
