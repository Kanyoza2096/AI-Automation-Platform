from core.request_context import RequestContext, clear_request_context, set_request_context
from plugins.industries.finance.plugin import FinancePlugin


def test_finance_manifest_is_formal():
    m = FinancePlugin(None, None).get_manifest()
    assert m.pack_id == "finance"
    assert "accounts" in m.resources
    assert "finance.initiate_payment" in m.external_actions
    assert "finance.payment_authorization" in m.policies


def test_finance_commands_declare_execution_semantics():
    commands = {x["intent"]: x for x in FinancePlugin(None, None).get_commands()}
    assert commands["get_account"]["execution_mode"] == "read_only"
    assert commands["record_transaction"]["execution_mode"] == "action_kernel"
    assert commands["initiate_payment"]["governed_action_type"] == "finance_initiate_payment"


def test_finance_workspace_is_authoritative():
    plugin = FinancePlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="finance-a", role="owner"))
    try:
        result = plugin.execute("create_account", {"workspace_id": "finance-b", "name": "Operating"}, "finance-b")
        assert result["workspace_id"] == "finance-a"
    finally:
        clear_request_context()


def test_finance_domain_flow_and_tenant_isolation():
    plugin = FinancePlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="finance-a", role="owner"))
    try:
        account = plugin.execute("create_account", {"name": "Operating"}, "finance-a")["data"]["account_id"]
        txn = plugin.execute("record_transaction", {"account_id": account, "amount": 125000, "direction": "credit", "reference": "INV-1"}, "finance-a")["data"]["transaction_id"]
        invoice = plugin.execute("create_invoice", {"customer_ref": "CUS-1", "amount": 50000}, "finance-a")["data"]["invoice_id"]
        assert plugin.execute("get_transaction", {"transaction_id": txn}, "finance-a")["ok"] is True
        assert plugin.execute("get_invoice", {"invoice_id": invoice}, "finance-a")["ok"] is True
        clear_request_context()
        set_request_context(RequestContext(principal_id="u-2", workspace_id="finance-b", role="owner"))
        assert plugin.execute("get_transaction", {"transaction_id": txn}, "finance-b")["ok"] is False
        assert plugin.execute("get_invoice", {"invoice_id": invoice}, "finance-b")["ok"] is False
    finally:
        clear_request_context()


def test_finance_payment_is_connector_deferred():
    plugin = FinancePlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="finance-a", role="owner"))
    try:
        result = plugin.execute("initiate_payment", {"amount": 10000, "currency": "MWK"}, "finance-a")
        assert result["status"] == "action_required"
        assert result["execution_mode"] == "action_kernel"
        assert "connector-deferred" in result["message"]
    finally:
        clear_request_context()
