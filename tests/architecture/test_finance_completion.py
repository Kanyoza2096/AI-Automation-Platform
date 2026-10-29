from core.request_context import RequestContext, clear_request_context, set_request_context
from plugins.industries.finance.plugin import FinancePlugin


def test_finance_completion_manifest():
    m = FinancePlugin(None, None).get_manifest()
    assert m.version == "1.1"
    assert "reconciliations" in m.resources
    assert "financial_outcomes" in m.resources
    assert "reconciliation" in m.capabilities
    assert "financial_outcomes" in m.capabilities


def test_finance_reconciliation_and_outcome_loop():
    plugin = FinancePlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="fin-a", role="owner"))
    try:
        account = plugin.execute("create_account", {"name": "Operating"}, "fin-a")["data"]["account_id"]
        rec = plugin.execute("record_reconciliation", {"account_id": account, "period": "2026-09", "expected_amount": 1000, "observed_amount": 995, "evidence_ref": "bank-1"}, "fin-a")
        assert rec["ok"] is True
        assert rec["data"]["status"] == "variance"
        out = plugin.execute("record_financial_outcome", {"reference_type": "reconciliation", "reference_id": rec["data"]["reconciliation_id"], "outcome_type": "reconciled", "amount": 995, "evidence_ref": "e-1"}, "fin-a")
        assert out["ok"] is True
        assert plugin.execute("get_financial_outcome", {"outcome_id": out["data"]["outcome_id"]}, "fin-a")["ok"] is True
    finally:
        clear_request_context()


def test_finance_completion_tenant_isolation():
    plugin = FinancePlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="fin-a", role="owner"))
    try:
        account = plugin.execute("create_account", {"name": "A"}, "fin-a")["data"]["account_id"]
        rec = plugin.execute("record_reconciliation", {"account_id": account, "period": "2026-09", "expected_amount": 10, "observed_amount": 10}, "fin-a")["data"]["reconciliation_id"]
        clear_request_context()
        set_request_context(RequestContext(principal_id="u-2", workspace_id="fin-b", role="owner"))
        assert plugin.execute("get_reconciliation", {"reconciliation_id": rec}, "fin-b")["ok"] is False
    finally:
        clear_request_context()
