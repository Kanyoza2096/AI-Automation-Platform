"""High-value Connector Fabric adapters.

All write operations use the shared EnterpriseProviderAdapter semantics:
no automatic mutation retry; ambiguous writes become ``uncertain`` and must
be reconciled.  These adapters intentionally expose curated KDO actions,
not entire vendor SDKs.
"""
from __future__ import annotations
from typing import Any, Optional
from services.connectors.enterprise_provider_base import EnterpriseProviderAdapter, ProviderAdapterError


class StripeAdapter(EnterpriseProviderAdapter):
    provider = "stripe"
    API_BASE = "https://api.stripe.com/v1"

    def __init__(self, *, token=None, base_url=API_BASE, **kwargs):
        super().__init__(token=token or __import__('os').getenv('STRIPE_SECRET_KEY', ''), base_url=base_url, **kwargs)

    def _headers(self):
        h = super()._headers(); h["Content-Type"] = "application/x-www-form-urlencoded"; return h

    def get_payment_intent(self, payment_intent_id: str):
        _, data, _ = self._request("GET", f"payment_intents/{payment_intent_id}"); return data

    def capture_payment_intent(self, payment_intent_id: str):
        self._require_mutation(high_risk=True, confirm=True)
        if not payment_intent_id: raise ValueError("payment_intent_id is required")
        try:
            _, data, headers = self._request("POST", f"payment_intents/{payment_intent_id}/capture", data="", mutation=True)
            return self._receipt(status="uncertain", object_id=payment_intent_id, request_id=headers.get("Request-Id"), metadata={"provider_status": (data or {}).get("status") if isinstance(data, dict) else None, "reconciliation_required": True})
        except ProviderAdapterError as exc: return self._handle_mutation_error(exc)

    def create_payment_intent(self, *, amount: int, currency: str, idempotency_key: str, customer: Optional[str] = None):
        self._require_mutation()
        if amount <= 0 or not currency or not idempotency_key: raise ValueError("amount, currency and idempotency_key are required")
        payload = {"amount": amount, "currency": currency.lower()}
        if customer: payload["customer"] = customer
        try:
            _, data, headers = self._request("POST", "payment_intents", data=__import__("urllib.parse").parse.urlencode(payload), headers={"Idempotency-Key": idempotency_key}, mutation=True)
            # Stripe's API is form encoded; retain an explicit boundary rather than silently pretending JSON was sent.
            return self._receipt(status="succeeded", object_id=data.get("id") if isinstance(data, dict) else None,
                                 request_id=headers.get("Request-Id"), metadata={"amount": amount, "currency": currency.lower()}) if isinstance(data, dict) and data.get("id") else self._receipt(status="uncertain", error="Stripe response lacked payment intent ID", metadata={"reconciliation_required": True})
        except ProviderAdapterError as exc: return self._handle_mutation_error(exc)


class ShopifyAdapter(EnterpriseProviderAdapter):
    provider = "shopify"
    def __init__(self, *, token=None, shop_domain: str, api_version="2026-07", base_url=None, **kwargs):
        if not shop_domain: raise ValueError("shop_domain is required")
        base_url = base_url or f"https://{shop_domain}/admin/api/{api_version}"
        self.shop_domain = shop_domain
        super().__init__(token=token or __import__('os').getenv('SHOPIFY_ACCESS_TOKEN', ''), base_url=base_url, **kwargs)
    def _headers(self):
        h = super()._headers(); h["X-Shopify-Access-Token"] = self._token; h.pop("Authorization", None); return h
    def get_order(self, order_id: str):
        _, data, _ = self._request("GET", f"orders/{order_id}.json"); return data
    def list_orders(self, *, status="any", limit=50):
        if not 1 <= limit <= 250: raise ValueError("limit must be 1..250")
        _, data, _ = self._request("GET", "orders.json", params={"status": status, "limit": limit}); return data
    def cancel_order(self, order_id: str, *, reason: Optional[str] = None, confirm=False):
        self._require_mutation(high_risk=True, confirm=confirm)
        try:
            _, data, headers = self._request("POST", f"orders/{order_id}/cancel.json", json={"reason": reason} if reason else {}, mutation=True)
            oid = (data or {}).get("order", {}).get("id") if isinstance(data, dict) else None
            return self._receipt(status="succeeded" if oid else "uncertain", object_id=oid, request_id=headers.get("X-Request-ID"), metadata={"reconciliation_required": not bool(oid)})
        except ProviderAdapterError as exc: return self._handle_mutation_error(exc)

    def _graphql(self, query: str, variables: dict[str, Any] | None = None, *, mutation: bool = False):
        _, data, headers = self._request("POST", "graphql.json", json={"query": query, "variables": variables or {}}, mutation=mutation)
        if not isinstance(data, dict):
            raise ProviderAdapterError(self.provider, "Shopify GraphQL returned an invalid response", ambiguous=mutation)
        errors = data.get("errors") or []
        if errors:
            raise ProviderAdapterError(self.provider, f"Shopify GraphQL error: {errors[0].get('message', 'unknown error')}", ambiguous=mutation)
        return data.get("data") or {}, headers

    def create_fulfillment(self, order_id: str, *, notify_customer: bool = False, message: str | None = None, confirm=False):
        self._require_mutation(high_risk=True, confirm=confirm)
        if not order_id: raise ValueError("order_id is required")
        query = """query($id: ID!) { order(id: $id) { fulfillmentOrders(first: 50) { nodes { id status assignedLocation { location { id } } } } } }"""
        mutation = """mutation($fulfillment: FulfillmentInput!, $message: String) { fulfillmentCreate(fulfillment: $fulfillment, message: $message) { fulfillment { id status displayStatus } userErrors { field message } } }"""
        try:
            data, _ = self._graphql(query, {"id": order_id})
            order = data.get("order") or {}
            nodes = [n for n in (order.get("fulfillmentOrders") or {}).get("nodes", []) if str(n.get("status", "")).upper() in {"OPEN", "SCHEDULED"}]
            if len(nodes) != 1:
                raise ValueError("Shopify order must have exactly one open/scheduled fulfillment order for governed fulfillment")
            fulfillment_input = {"lineItemsByFulfillmentOrder": [{"fulfillmentOrderId": nodes[0]["id"]}], "notifyCustomer": bool(notify_customer)}
            data, headers = self._graphql(mutation, {"fulfillment": fulfillment_input, "message": message}, mutation=True)
            payload = data.get("fulfillmentCreate") or {}
            user_errors = payload.get("userErrors") or []
            if user_errors:
                return self._receipt(status="failed", error=str(user_errors[0].get("message", "Shopify fulfillment rejected")), request_id=headers.get("X-Request-Id"), metadata={"user_errors": user_errors})
            fulfillment = payload.get("fulfillment") or {}
            oid = fulfillment.get("id")
            return self._receipt(status="uncertain", object_id=oid, request_id=headers.get("X-Request-Id"), metadata={"provider_status": fulfillment.get("status"), "display_status": fulfillment.get("displayStatus"), "fulfillment_order_id": nodes[0]["id"], "reconciliation_required": True})
        except ProviderAdapterError as exc:
            return self._handle_mutation_error(exc)

    def reconcile_fulfillment(self, fulfillment_id: str):
        if not fulfillment_id: raise ValueError("fulfillment_id is required")
        query = """query($id: ID!) { fulfillment(id: $id) { id status displayStatus trackingInfo(first: 10) { company number url } order { id } } }"""
        data, _ = self._graphql(query, {"id": fulfillment_id})
        fulfillment = data.get("fulfillment")
        if not fulfillment:
            return {"status": "unknown", "provider_reference": fulfillment_id, "observed": None}
        provider_status = str(fulfillment.get("status") or "").upper()
        canonical = {"SUCCESS": "succeeded", "FAILURE": "failed", "ERROR": "failed", "CANCELLED": "failed"}.get(provider_status, "in_progress")
        return {"status": canonical, "provider_reference": fulfillment.get("id") or fulfillment_id, "provider_status": provider_status, "display_status": fulfillment.get("displayStatus"), "observed": fulfillment}


class SlackAdapter(EnterpriseProviderAdapter):
    provider = "slack"; API_BASE = "https://slack.com/api"
    def __init__(self, *, token=None, base_url=API_BASE, **kwargs): super().__init__(token=token or __import__('os').getenv('SLACK_BOT_TOKEN', ''), base_url=base_url, **kwargs)
    def post_message(self, *, channel: str, text: str):
        self._require_mutation()
        if not channel or not text.strip(): raise ValueError("channel and text are required")
        try:
            _, data, _ = self._request("POST", "chat.postMessage", json={"channel": channel, "text": text})
            if not isinstance(data, dict) or not data.get("ok") or not data.get("ts"):
                return self._receipt(status="failed", error=str((data or {}).get("error", "Slack rejected message")), metadata={"response": data})
            return self._receipt(status="succeeded", object_id=data["ts"], metadata={"channel": channel})
        except ProviderAdapterError as exc: return self._handle_mutation_error(exc)
    def list_channels(self, *, limit=100):
        _, data, _ = self._request("GET", "conversations.list", params={"limit": limit}); return data


class JiraAdapter(EnterpriseProviderAdapter):
    provider = "jira"
    def __init__(self, *, token=None, base_url: str, **kwargs):
        if not base_url: raise ValueError("Jira base_url is required")
        super().__init__(token=token or __import__('os').getenv('JIRA_TOKEN', ''), base_url=base_url.rstrip('/') + '/rest/api/3', **kwargs)
    def get_issue(self, issue_key: str):
        _, data, _ = self._request("GET", f"issue/{issue_key}"); return data
    def create_issue(self, *, project_key: str, issue_type: str, summary: str, description: str = ""):
        self._require_mutation()
        payload={"fields":{"project":{"key":project_key},"issuetype":{"name":issue_type},"summary":summary,"description":description}}
        try:
            _, data, _ = self._request("POST", "issue", json=payload, mutation=True)
            oid = (data or {}).get("id") if isinstance(data, dict) else None
            return self._receipt(status="succeeded" if oid else "uncertain", object_id=oid, metadata={"issue_key": (data or {}).get("key"), "reconciliation_required": not bool(oid)})
        except ProviderAdapterError as exc: return self._handle_mutation_error(exc)


class PagerDutyAdapter(EnterpriseProviderAdapter):
    provider = "pagerduty"; API_BASE = "https://api.pagerduty.com"
    def __init__(self, *, token=None, base_url=API_BASE, **kwargs): super().__init__(token=token or __import__('os').getenv('PAGERDUTY_TOKEN', ''), base_url=base_url, **kwargs)
    def _headers(self):
        h=super()._headers(); h["Authorization"] = f"Token token={self._token}"; h["From"] = "kdo-connector"; return h
    def create_incident(self, *, service_id: str, title: str, urgency="high"):
        self._require_mutation(high_risk=True, confirm=True)
        try:
            _, data, headers=self._request("POST", "incidents", json={"incident":{"type":"incident","title":title,"service":{"id":service_id,"type":"service_reference"},"urgency":urgency}}, mutation=True)
            oid=(data or {}).get("incident",{}).get("id") if isinstance(data,dict) else None
            return self._receipt(status="succeeded" if oid else "uncertain", object_id=oid, request_id=headers.get("X-Request-Id"), metadata={"reconciliation_required":not bool(oid)})
        except ProviderAdapterError as exc:return self._handle_mutation_error(exc)
    def get_incident(self, incident_id):
        _,data,_=self._request("GET",f"incidents/{incident_id}"); return data


class DatadogAdapter(EnterpriseProviderAdapter):
    provider = "datadog"; API_BASE = "https://api.datadoghq.com/api/v2"
    def __init__(self, *, api_key=None, app_key=None, base_url=API_BASE, **kwargs):
        import os
        self.api_key=api_key or os.getenv("DD_API_KEY", ""); self.app_key=app_key or os.getenv("DD_APP_KEY", "")
        if not self.api_key or not self.app_key: raise ValueError("Datadog API and application keys are required")
        super().__init__(token=self.api_key, base_url=base_url, **kwargs)
    def _headers(self):
        h=super()._headers(); h["DD-API-KEY"]=self.api_key; h["DD-APPLICATION-KEY"]=self.app_key; return h
    def get_monitors(self):
        _,data,_=self._request("GET","monitor"); return data
    def create_downtime(self, *, scope: str, message: str):
        self._require_mutation()
        try:
            _,data,headers=self._request("POST","downtime",json={"data":{"type":"downtime","attributes":{"message":message,"scope":scope}}},mutation=True)
            oid=(data or {}).get("data",{}).get("id") if isinstance(data,dict) else None
            return self._receipt(status="succeeded" if oid else "uncertain",object_id=oid,request_id=headers.get("X-Request-Id"),metadata={"reconciliation_required":not bool(oid)})
        except ProviderAdapterError as exc:return self._handle_mutation_error(exc)


class CloudflareAdapter(EnterpriseProviderAdapter):
    provider = "cloudflare"; API_BASE = "https://api.cloudflare.com/client/v4"
    def __init__(self, *, token=None, base_url=API_BASE, **kwargs): super().__init__(token=token or __import__('os').getenv('CLOUDFLARE_API_TOKEN', ''), base_url=base_url, **kwargs)
    def get_zone(self, zone_id):
        _,data,_=self._request("GET",f"zones/{zone_id}"); return data
    def purge_cache(self, zone_id, *, confirm=False):
        self._require_mutation(high_risk=True,confirm=confirm)
        try:
            _,data,headers=self._request("POST",f"zones/{zone_id}/purge_cache",json={"purge_everything":True},mutation=True)
            oid=(data or {}).get("id") or zone_id if isinstance(data,dict) else None
            return self._receipt(status="uncertain",object_id=None,request_id=headers.get("CF-RAY"),error="Cloudflare accepted purge request without a durable purge object ID; verify cache state before terminal success",metadata={"zone_id":zone_id,"reconciliation_required":True,"provider_success":bool(data and data.get("success"))})
        except ProviderAdapterError as exc:return self._handle_mutation_error(exc)


class TwilioAdapter(EnterpriseProviderAdapter):
    provider = "twilio"
    def __init__(self, *, account_sid=None, auth_token=None, base_url=None, **kwargs):
        import os
        self.account_sid=account_sid or os.getenv("TWILIO_ACCOUNT_SID", ""); auth_token=auth_token or os.getenv("TWILIO_AUTH_TOKEN", "")
        if not self.account_sid: raise ValueError("Twilio account_sid is required")
        super().__init__(token=auth_token,base_url=base_url or f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}",**kwargs)
    def _headers(self):
        import base64
        h=super()._headers(); h["Authorization"]="Basic "+base64.b64encode(f"{self.account_sid}:{self._token}".encode()).decode(); return h
    def get_message(self, message_sid):
        _,data,_=self._request("GET",f"Messages/{message_sid}.json"); return data
    def send_sms(self, *, from_: str, to: str, body: str):
        self._require_mutation()
        if not from_ or not to or not body.strip(): raise ValueError("from, to and body are required")
        try:
            _,data,headers=self._request("POST","Messages.json",data=__import__("urllib.parse").parse.urlencode({"From":from_,"To":to,"Body":body}),headers={"Content-Type":"application/x-www-form-urlencoded"},mutation=True)
            oid=(data or {}).get("sid") if isinstance(data,dict) else None
            return self._receipt(status="succeeded" if oid else "uncertain",object_id=oid,request_id=headers.get("Twilio-Request-Id"),metadata={"to":to,"reconciliation_required":not bool(oid)})
        except ProviderAdapterError as exc:return self._handle_mutation_error(exc)
