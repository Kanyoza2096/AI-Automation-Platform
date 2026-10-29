"""Governed finance operations commands."""


class FinanceCommands:
    def __init__(self, repository, redis_client=None):
        self.repo = repository
        self.redis = redis_client

    def list_all(self):
        return [
            {"intent": "create_account", "execution_mode": "action_kernel", "governed_action_type": "finance_create_account"},
            {"intent": "get_account", "execution_mode": "read_only"},
            {"intent": "record_transaction", "execution_mode": "action_kernel", "governed_action_type": "finance_record_transaction"},
            {"intent": "get_transaction", "execution_mode": "read_only"},
            {"intent": "create_invoice", "execution_mode": "action_kernel", "governed_action_type": "finance_create_invoice"},
            {"intent": "get_invoice", "execution_mode": "read_only"},
            {"intent": "record_reconciliation", "execution_mode": "action_kernel", "governed_action_type": "finance_record_reconciliation"},
            {"intent": "get_reconciliation", "execution_mode": "read_only"},
            {"intent": "record_financial_outcome", "execution_mode": "action_kernel", "governed_action_type": "finance_record_outcome"},
            {"intent": "get_financial_outcome", "execution_mode": "read_only"},
            {"intent": "initiate_payment", "execution_mode": "action_kernel", "governed_action_type": "finance_initiate_payment"},
            {"intent": "send_statement", "execution_mode": "action_kernel", "governed_action_type": "finance_send_statement"},
        ]

    def handle(self, command, params, workspace_id):
        handler = getattr(self, f"_{command}", None)
        if not handler:
            return {"ok": False, "workspace_id": workspace_id, "error": f"Unknown finance command: {command}"}
        return handler(params, workspace_id)

    def _create_account(self, p, ws):
        try:
            obj = self.repo.create_account(ws, p)
            return {"ok": True, "workspace_id": ws, "data": {"account_id": obj.id, "name": obj.name}}
        except ValueError as exc:
            return {"ok": False, "workspace_id": ws, "error": str(exc)}

    def _get_account(self, p, ws):
        obj = self.repo.get_account(ws, str(p.get("account_id", "")))
        return {"ok": obj is not None, "workspace_id": ws, "data": obj.model_dump(mode="json") if obj else None, "error": None if obj else "Account not found"}

    def _record_transaction(self, p, ws):
        try:
            obj = self.repo.record_transaction(ws, p)
            return {"ok": True, "workspace_id": ws, "data": {"transaction_id": obj.id, "status": obj.status}}
        except ValueError as exc:
            return {"ok": False, "workspace_id": ws, "error": str(exc)}

    def _get_transaction(self, p, ws):
        obj = self.repo.get_transaction(ws, str(p.get("transaction_id", "")))
        return {"ok": obj is not None, "workspace_id": ws, "data": obj.model_dump(mode="json") if obj else None, "error": None if obj else "Transaction not found"}

    def _create_invoice(self, p, ws):
        try:
            obj = self.repo.create_invoice(ws, p)
            return {"ok": True, "workspace_id": ws, "data": {"invoice_id": obj.id, "status": obj.status, "amount": obj.amount}}
        except ValueError as exc:
            return {"ok": False, "workspace_id": ws, "error": str(exc)}

    def _get_invoice(self, p, ws):
        obj = self.repo.get_invoice(ws, str(p.get("invoice_id", "")))
        return {"ok": obj is not None, "workspace_id": ws, "data": obj.model_dump(mode="json") if obj else None, "error": None if obj else "Invoice not found"}

    def _record_reconciliation(self, p, ws):
        try:
            obj = self.repo.record_reconciliation(ws, p)
            return {"ok": True, "workspace_id": ws, "data": {"reconciliation_id": obj.id, "status": obj.status, "variance": obj.variance}}
        except ValueError as exc:
            return {"ok": False, "workspace_id": ws, "error": str(exc)}

    def _get_reconciliation(self, p, ws):
        obj = self.repo.get_reconciliation(ws, str(p.get("reconciliation_id", "")))
        return {"ok": obj is not None, "workspace_id": ws, "data": obj.model_dump(mode="json") if obj else None, "error": None if obj else "Reconciliation not found"}

    def _record_financial_outcome(self, p, ws):
        try:
            obj = self.repo.record_outcome(ws, p)
            return {"ok": True, "workspace_id": ws, "data": {"outcome_id": obj.id, "status": obj.status}}
        except ValueError as exc:
            return {"ok": False, "workspace_id": ws, "error": str(exc)}

    def _get_financial_outcome(self, p, ws):
        obj = self.repo.get_outcome(ws, str(p.get("outcome_id", "")))
        return {"ok": obj is not None, "workspace_id": ws, "data": obj.model_dump(mode="json") if obj else None, "error": None if obj else "Financial outcome not found"}

    def _initiate_payment(self, p, ws):
        amount = float(p.get("amount", 0))
        if amount <= 0:
            return {"ok": False, "workspace_id": ws, "error": "Payment amount must be positive"}
        return {"ok": True, "workspace_id": ws, "status": "action_required", "execution_mode": "action_kernel", "action_type": "finance_initiate_payment", "message": "Payment initiation is connector-deferred; no funds movement occurs in the sector pack", "data": {"amount": amount, "currency": str(p.get("currency", "MWK")).upper(), "reference": p.get("reference")}}

    def _send_statement(self, p, ws):
        return {"ok": True, "workspace_id": ws, "status": "action_required", "execution_mode": "action_kernel", "action_type": "finance_send_statement", "message": "Statement delivery is connector-deferred; no delivery occurs inside the sector pack", "data": {"account_id": p.get("account_id"), "destination": p.get("destination")}}
