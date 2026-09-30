"""Finance repository with durable Supabase and test-friendly memory storage."""
import logging
import uuid
from typing import Optional
from core.tenant_repository import require_workspace_id
from .models import Account, Transaction, Invoice, Reconciliation, FinancialOutcome

logger = logging.getLogger("kanyoza.plugins.finance")


class FinanceRepository:
    TABLES = {
        Account: "finance_accounts",
        Transaction: "finance_transactions",
        Invoice: "finance_invoices",
        Reconciliation: "finance_reconciliations",
        FinancialOutcome: "finance_outcomes",
    }

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._memory: dict[str, object] = {}

    @staticmethod
    def _production() -> bool:
        try:
            from config.settings import settings
            import os
            mode = os.getenv("SERVICE_MODE", "").strip().lower()
            return bool(getattr(settings, "is_production", False)) and mode in {
                "full", "api", "core", "director", "meta", "condition", "workers", "monitor", "github", "chaos",
            }
        except Exception:
            return False

    def _require_durable_write(self, operation: str) -> None:
        if self.supabase is None and self._production():
            raise RuntimeError(f"durable finance database is required for {operation}")

    def _put(self, obj):
        self._require_durable_write(f"write_{type(obj).__name__.lower()}")
        if self.supabase:
            try:
                self.supabase.table(self.TABLES[type(obj)]).insert(obj.model_dump(mode="json")).execute()
            except Exception as exc:
                logger.error("Finance persistence failed: %s", exc)
                raise RuntimeError("finance persistence failed") from exc
        self._memory[obj.id] = obj
        return obj

    def _get(self, cls, workspace_id: str, object_id: str):
        workspace_id = require_workspace_id(workspace_id, op=f"get_{cls.__name__.lower()}")
        obj = self._memory.get(object_id)
        if isinstance(obj, cls) and obj.workspace_id == workspace_id:
            return obj
        if self.supabase:
            try:
                result = self.supabase.table(self.TABLES[cls]).select("*").eq("workspace_id", workspace_id).eq("id", object_id).limit(1).execute()
                if result.data:
                    return cls(**result.data[0])
            except Exception as exc:
                logger.warning("Finance lookup failed: %s", exc)
        return None

    def create_account(self, workspace_id: str, data: dict) -> Account:
        workspace_id = require_workspace_id(workspace_id, op="create_account")
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError("account name is required")
        return self._put(Account(
            id=f"fin-acc-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            name=name, account_type=str(data.get("account_type", "operating")),
            currency=str(data.get("currency", "MWK")).upper(),
        ))

    def get_account(self, workspace_id: str, account_id: str) -> Optional[Account]:
        return self._get(Account, workspace_id, account_id)

    def record_transaction(self, workspace_id: str, data: dict) -> Transaction:
        workspace_id = require_workspace_id(workspace_id, op="record_transaction")
        account_id = str(data.get("account_id", ""))
        if self.get_account(workspace_id, account_id) is None:
            raise ValueError("account not found")
        amount = float(data.get("amount", 0))
        if amount <= 0:
            raise ValueError("transaction amount must be positive")
        direction = str(data.get("direction", "")).lower()
        if direction not in {"credit", "debit"}:
            raise ValueError("direction must be credit or debit")
        return self._put(Transaction(
            id=f"fin-txn-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            account_id=account_id, amount=amount, direction=direction,
            reference=str(data.get("reference", "")),
        ))

    def get_transaction(self, workspace_id: str, transaction_id: str) -> Optional[Transaction]:
        return self._get(Transaction, workspace_id, transaction_id)

    def create_invoice(self, workspace_id: str, data: dict) -> Invoice:
        workspace_id = require_workspace_id(workspace_id, op="create_invoice")
        customer_ref = str(data.get("customer_ref", "")).strip()
        amount = float(data.get("amount", 0))
        if not customer_ref:
            raise ValueError("customer_ref is required")
        if amount <= 0:
            raise ValueError("invoice amount must be positive")
        return self._put(Invoice(
            id=f"fin-inv-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            customer_ref=customer_ref, amount=amount,
            currency=str(data.get("currency", "MWK")).upper(), due_date=data.get("due_date"),
        ))

    def get_invoice(self, workspace_id: str, invoice_id: str) -> Optional[Invoice]:
        return self._get(Invoice, workspace_id, invoice_id)


    def record_reconciliation(self, workspace_id: str, data: dict) -> Reconciliation:
        workspace_id = require_workspace_id(workspace_id, op="record_reconciliation")
        account_id = str(data.get("account_id", ""))
        if self.get_account(workspace_id, account_id) is None:
            raise ValueError("account not found")
        expected = float(data.get("expected_amount", 0))
        observed = float(data.get("observed_amount", 0))
        if expected < 0 or observed < 0:
            raise ValueError("reconciliation amounts must be non-negative")
        variance = round(observed - expected, 2)
        status = "matched" if variance == 0 else "variance"
        return self._put(Reconciliation(
            id=f"fin-rec-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            account_id=account_id, period=str(data.get("period", "")).strip(),
            status=status, expected_amount=expected, observed_amount=observed,
            variance=variance, evidence_ref=str(data.get("evidence_ref", "")),
        ))

    def get_reconciliation(self, workspace_id: str, reconciliation_id: str) -> Optional[Reconciliation]:
        return self._get(Reconciliation, workspace_id, reconciliation_id)

    def record_outcome(self, workspace_id: str, data: dict) -> FinancialOutcome:
        workspace_id = require_workspace_id(workspace_id, op="record_financial_outcome")
        reference_id = str(data.get("reference_id", "")).strip()
        outcome_type = str(data.get("outcome_type", "")).strip()
        if not reference_id or not outcome_type:
            raise ValueError("reference_id and outcome_type are required")
        amount = data.get("amount")
        if amount is not None and float(amount) < 0:
            raise ValueError("outcome amount must be non-negative")
        return self._put(FinancialOutcome(
            id=f"fin-out-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            reference_type=str(data.get("reference_type", "transaction")),
            reference_id=reference_id, outcome_type=outcome_type,
            status=str(data.get("status", "recorded")),
            amount=float(amount) if amount is not None else None,
            evidence_ref=str(data.get("evidence_ref", "")),
        ))

    def get_outcome(self, workspace_id: str, outcome_id: str) -> Optional[FinancialOutcome]:
        return self._get(FinancialOutcome, workspace_id, outcome_id)
