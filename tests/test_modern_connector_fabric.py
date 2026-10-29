import httpx
from services.connectors.modern_adapters import StripeAdapter, ShopifyAdapter, SlackAdapter, JiraAdapter, PagerDutyAdapter, DatadogAdapter, CloudflareAdapter, TwilioAdapter
from services.connectors.aws_adapter import AWSAdapter


def client(handler): return httpx.Client(transport=httpx.MockTransport(handler))

def test_stripe_success_requires_provider_id_and_uses_idempotency_key():
    seen={}
    def h(r):
        seen["key"]=r.headers.get("Idempotency-Key"); return httpx.Response(200,json={"id":"pi_123"})
    a=StripeAdapter(token="x",client=client(h),allow_mutations=True)
    r=a.create_payment_intent(amount=1000,currency="USD",idempotency_key="idem-1")
    assert r["status"]=="succeeded" and r["provider_object_id"]=="pi_123" and seen["key"]=="idem-1"

def test_slack_message_receipt():
    a=SlackAdapter(token="x",client=client(lambda r:httpx.Response(200,json={"ok":True,"ts":"171.1"})),allow_mutations=True)
    assert a.post_message(channel="#ops",text="hello")["provider_object_id"]=="171.1"

def test_shopify_cancel_is_high_risk():
    a=ShopifyAdapter(token="x",shop_domain="example.myshopify.com",client=client(lambda r:httpx.Response(200,json={"order":{"id":"99"}})),allow_mutations=True,allow_high_risk=True)
    assert a.cancel_order("99",confirm=True)["status"]=="succeeded"

def test_jira_create_issue():
    a=JiraAdapter(token="x",base_url="https://example.atlassian.net",client=client(lambda r:httpx.Response(201,json={"id":"10001","key":"OPS-1"})),allow_mutations=True)
    r=a.create_issue(project_key="OPS",issue_type="Task",summary="Incident")
    assert r["provider_object_id"]=="10001" and r["metadata"]["issue_key"]=="OPS-1"

def test_pagerduty_incident_requires_high_risk_gate():
    a=PagerDutyAdapter(token="x",client=client(lambda r:httpx.Response(201,json={"incident":{"id":"P1"}})),allow_mutations=True,allow_high_risk=True)
    assert a.create_incident(service_id="S1",title="down")["provider_object_id"]=="P1"

def test_datadog_downtime():
    a=DatadogAdapter(api_key="a",app_key="b",client=client(lambda r:httpx.Response(200,json={"data":{"id":"dt1"}})),allow_mutations=True)
    assert a.create_downtime(scope="host:web",message="deploy")["provider_object_id"]=="dt1"

def test_cloudflare_purge_stays_uncertain_without_receipt_id():
    a=CloudflareAdapter(token="x",client=client(lambda r:httpx.Response(200,json={"success":True,"result":{}})),allow_mutations=True,allow_high_risk=True)
    r=a.purge_cache("zone1",confirm=True)
    assert r["status"]=="uncertain" and r["provider_object_id"] is None and r["metadata"]["reconciliation_required"]

def test_twilio_send_sms():
    a=TwilioAdapter(account_sid="AC1",auth_token="secret",client=client(lambda r:httpx.Response(201,json={"sid":"SM1"})),allow_mutations=True)
    r=a.send_sms(from_="+100",to="+200",body="hello")
    assert r["provider_object_id"]=="SM1"

def test_aws_fake_client_receipt_and_no_second_execution_authority():
    class ECS:
        def update_service(self, **kw): return {"service":{"serviceArn":"arn:aws:ecs:svc/1"}}
    a=AWSAdapter(region="us-east-1",clients={"ecs":ECS()},allow_mutations=True)
    r=a.update_ecs_service(cluster="c",service="s",desired_count=2,confirm=True)
    assert r["status"]=="succeeded" and r["provider_object_id"].startswith("arn:")


def test_google_workspace_drive_lookup():
    from services.connectors.google_workspace_adapter import GoogleWorkspaceAdapter
    a = GoogleWorkspaceAdapter(token="x", client=client(lambda r: httpx.Response(200, json={"id": "file-1", "name": "plan.docx"})))
    r = a.get_drive_file("file-1")
    assert r["status"] == "succeeded" and r["provider_object_id"] == "file-1"


def test_google_workspace_gmail_send_has_receipt_and_gate():
    from services.connectors.google_workspace_adapter import GoogleWorkspaceAdapter
    a = GoogleWorkspaceAdapter(token="x", client=client(lambda r: httpx.Response(200, json={"id": "msg-1", "threadId": "thr-1"})), allow_mutations=True)
    r = a.send_gmail_message(raw_message="base64url-rfc822", confirm=True)
    assert r["provider_object_id"] == "msg-1" and r["metadata"]["thread_id"] == "thr-1"


def test_google_workspace_calendar_event():
    from services.connectors.google_workspace_adapter import GoogleWorkspaceAdapter
    a = GoogleWorkspaceAdapter(token="x", client=client(lambda r: httpx.Response(200, json={"id": "event-1", "htmlLink": "https://calendar.google.com/event"})), allow_mutations=True)
    r = a.create_calendar_event(calendar_id="primary", event={"summary": "KDO"}, confirm=True)
    assert r["provider_object_id"] == "event-1"


def test_google_workspace_sheets_without_durable_identity_stays_uncertain():
    from services.connectors.google_workspace_adapter import GoogleWorkspaceAdapter
    a = GoogleWorkspaceAdapter(token="x", client=client(lambda r: httpx.Response(200, json={"updates": {}})), allow_mutations=True)
    r = a.append_sheet_values(spreadsheet_id="sheet-1", range_name="A1", values=[["KDO"]], confirm=True)
    assert r["status"] == "uncertain" and r["provider_object_id"] is None and r["metadata"]["reconciliation_required"]


def test_google_workspace_docs_create():
    from services.connectors.google_workspace_adapter import GoogleWorkspaceAdapter
    a = GoogleWorkspaceAdapter(token="x", client=client(lambda r: httpx.Response(200, json={"documentId": "doc-1"})), allow_mutations=True)
    r = a.create_doc(title="KDO Runbook", confirm=True)
    assert r["provider_object_id"] == "doc-1"
