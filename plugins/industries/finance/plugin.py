"""Finance & Financial Operations sector pack."""
from plugins.industries.base import IndustryPlugin


class FinancePlugin(IndustryPlugin):
    pack_id = "finance"
    pack_version = "1.1"
    display_name = "Finance & Financial Operations"
    capabilities = ("accounting_operations", "transaction_records", "invoicing", "payment_governance", "statement_delivery", "reconciliation", "financial_outcomes")
    resources = ("accounts", "transactions", "invoices", "reconciliations", "financial_outcomes")
    policies = ("finance.workspace_access", "finance.account_access", "finance.transaction_authorization", "finance.payment_authorization")
    workflows = ("account_lifecycle", "transaction_lifecycle", "invoice_lifecycle", "payment_governance", "statement_delivery", "reconciliation_lifecycle", "financial_outcome_loop")
    events = ("account.created", "transaction.recorded", "invoice.created", "payment.requested", "statement.requested", "reconciliation.recorded", "financial_outcome.recorded")
    external_actions = ("finance.initiate_payment", "finance.send_statement")

    def __init__(self, supabase_client=None, redis_client=None):
        super().__init__(supabase_client, redis_client)
        from .repository import FinanceRepository
        from .commands import FinanceCommands
        self.repo = FinanceRepository(supabase_client)
        self.commands_handler = FinanceCommands(self.repo, redis_client)

    def get_commands(self):
        return self.commands_handler.list_all()

    def execute(self, command, params, workspace_id="default"):
        workspace_id = self.resolve_workspace(workspace_id)
        safe_params = {k: v for k, v in (params or {}).items() if k != "workspace_id"}
        return self.commands_handler.handle(command, safe_params, workspace_id)

    def get_webhooks(self):
        return [
            {"path": "/webhooks/finance/payment", "method": "POST", "handler": "handle_payment_webhook", "description": "External payment lifecycle event"},
            {"path": "/webhooks/finance/transaction", "method": "POST", "handler": "handle_transaction_webhook", "description": "External transaction reconciliation event"},
        ]
