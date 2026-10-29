"""Finance repository with durable Supabase and test-friendly memory storage."""
import logging
import uuid
from typing import Optional
from core.tenant_repository import require_workspace_id
from .models import Account, Transaction, Invoice, Reconciliation, Payment, PaymentReconciliationEvidence, FinancialOutcome, StatementDelivery, StatementReconciliationEvidence

logger = logging.getLogger("kanyoza.plugins.finance")


class FinanceRepository:
    TABLES = {
        Account: "finance_accounts",
        Transaction: "finance_transactions",
        Invoice: "finance_invoices",
        Reconciliation: "finance_reconciliations",
        FinancialOutcome: "finance_outcomes",
        Payment: "finance_payments",
        PaymentReconciliationEvidence: "finance_payment_reconciliation_evidence",
        StatementDelivery: "finance_statement_deliveries",
        StatementReconciliationEvidence: "finance_statement_reconciliation_evidence",
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


    def create_payment(self, workspace_id: str, data: dict) -> Payment:
        workspace_id = require_workspace_id(workspace_id, op="create_payment")
        amount = float(data.get("amount", 0))
        currency = str(data.get("currency", "")).upper().strip()
        reference = str(data.get("reference", "")).strip()
        idempotency_key = str(data.get("idempotency_key", "")).strip()
        if amount <= 0:
            raise ValueError("payment amount must be positive")
        if len(currency) != 3:
            raise ValueError("payment currency must be a 3-letter code")
        if not reference:
            raise ValueError("payment reference is required")
        if not idempotency_key:
            raise ValueError("payment idempotency_key is required")
        return self._put(Payment(
            id=f"fin-pay-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            amount=amount, currency=currency, reference=reference,
            idempotency_key=idempotency_key,
        ))

    def get_payment(self, workspace_id: str, payment_id: str) -> Optional[Payment]:
        return self._get(Payment, workspace_id, payment_id)

    def update_payment(self, workspace_id: str, payment_id: str, **changes) -> Payment:
        payment = self.get_payment(workspace_id, payment_id)
        if payment is None:
            raise ValueError("payment not found in workspace")
        updated = payment.model_copy(update={**changes, "updated_at": Payment.utcnow()})
        self._require_durable_write("update_payment")
        if self.supabase:
            try:
                self.supabase.table(self.TABLES[Payment]).update(updated.model_dump(mode="json")).eq("workspace_id", workspace_id).eq("id", payment_id).execute()
            except Exception as exc:
                logger.error("Finance payment persistence failed: %s", exc)
                raise RuntimeError("finance payment persistence failed") from exc
        self._memory[payment_id] = updated
        return updated

    def list_reconciliation_required_payments(self, workspace_id: str | None = None, limit: int = 100):
        if workspace_id is not None:
            workspace_id = require_workspace_id(workspace_id, op="list_reconciliation_required_payments")
        if self.supabase:
            try:
                query = (self.supabase.table(self.TABLES[Payment]).select("*")
                         .eq("reconciliation_required", True).order("created_at").limit(limit))
                if workspace_id is not None:
                    query = query.eq("workspace_id", workspace_id)
                result = query.execute()
                return [Payment(**row) for row in (result.data or [])]
            except Exception as exc:
                logger.warning("Finance pending payment lookup failed: %s", exc)
        rows = [x for x in self._memory.values() if isinstance(x, Payment) and x.reconciliation_required
                and (workspace_id is None or x.workspace_id == workspace_id)]
        return sorted(rows, key=lambda x: x.created_at)[:limit]

    def record_payment_reconciliation_evidence(self, workspace_id: str, data: dict) -> PaymentReconciliationEvidence:
        workspace_id = require_workspace_id(workspace_id, op="record_payment_reconciliation_evidence")
        self._require_durable_write("record_payment_reconciliation_evidence")
        payment_id = str(data.get("payment_id", ""))
        if self.get_payment(workspace_id, payment_id) is None:
            raise ValueError("payment not found in workspace")
        row = PaymentReconciliationEvidence(
            id=f"fin-pay-rec-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            payment_id=payment_id, provider=str(data.get("provider", "stripe")),
            provider_reference=str(data.get("provider_reference", "")),
            attempt_number=int(data.get("attempt_number", 0)),
            provider_status=str(data.get("provider_status", "unknown")),
            canonical_status=str(data.get("canonical_status", "uncertain")),
            terminal=bool(data.get("terminal", False)),
            verification_state=str(data.get("verification_state", "provider_observed_nonterminal")),
            observed=dict(data.get("observed") or {}), evidence=dict(data.get("evidence") or {}),
        )
        if self.supabase:
            try:
                self.supabase.table(self.TABLES[PaymentReconciliationEvidence]).insert(row.model_dump(mode="json")).execute()
            except Exception as exc:
                logger.error("Finance reconciliation evidence persistence failed: %s", exc)
                raise RuntimeError("finance reconciliation evidence persistence failed") from exc
        self._memory[row.id] = row
        return row

    def list_payment_reconciliation_evidence(self, workspace_id: str, payment_id: str, limit: int = 100):
        workspace_id = require_workspace_id(workspace_id, op="list_payment_reconciliation_evidence")
        if self.supabase:
            try:
                result = (self.supabase.table(self.TABLES[PaymentReconciliationEvidence]).select("*")
                          .eq("workspace_id", workspace_id).eq("payment_id", payment_id)
                          .order("attempt_number", desc=True).limit(limit).execute())
                return [PaymentReconciliationEvidence(**row) for row in (result.data or [])]
            except Exception as exc:
                logger.warning("Finance reconciliation evidence lookup failed: %s", exc)
        rows = [x for x in self._memory.values() if isinstance(x, PaymentReconciliationEvidence)
                and x.workspace_id == workspace_id and x.payment_id == payment_id]
        return sorted(rows, key=lambda x: x.attempt_number, reverse=True)[:limit]

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

    def create_statement_delivery(self, workspace_id: str, data: dict) -> StatementDelivery:
        workspace_id = require_workspace_id(workspace_id, op="create_statement_delivery")
        account_id = str(data.get("account_id", "")).strip()
        if self.get_account(workspace_id, account_id) is None:
            raise ValueError("account not found")
        to_number = str(data.get("to_number", "")).strip()
        body = str(data.get("body", "")).strip()
        key = str(data.get("idempotency_key", "")).strip()
        if not to_number or not body or not key:
            raise ValueError("to_number, body and idempotency_key are required")
        return self._put(StatementDelivery(id=f"fin-stmt-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, account_id=account_id, to_number=to_number, body=body, idempotency_key=key, provider=str(data.get("provider", "twilio")), status=str(data.get("status", "pending")), reconciliation_required=bool(data.get("reconciliation_required", True)), evidence=dict(data.get("evidence") or {})))

    def get_statement_delivery(self, workspace_id: str, delivery_id: str) -> Optional[StatementDelivery]:
        return self._get(StatementDelivery, workspace_id, delivery_id)

    def get_statement_delivery_by_idempotency_key(self, workspace_id: str, key: str) -> Optional[StatementDelivery]:
        workspace_id = require_workspace_id(workspace_id, op="get_statement_delivery_by_idempotency_key")
        for obj in self._memory.values():
            if isinstance(obj, StatementDelivery) and obj.workspace_id == workspace_id and obj.idempotency_key == key:
                return obj
        if self.supabase:
            result = self.supabase.table(self.TABLES[StatementDelivery]).select("*").eq("workspace_id", workspace_id).eq("idempotency_key", key).limit(1).execute()
            if result.data:
                return StatementDelivery(**result.data[0])
        return None

    def update_statement_delivery(self, workspace_id: str, delivery_id: str, **changes) -> StatementDelivery:
        delivery = self.get_statement_delivery(workspace_id, delivery_id)
        if delivery is None:
            raise ValueError("statement delivery not found")
        updated = delivery.model_copy(update={**changes, "updated_at": StatementDelivery.utcnow()})
        self._require_durable_write("update_statement_delivery")
        if self.supabase:
            self.supabase.table(self.TABLES[StatementDelivery]).update(updated.model_dump(mode="json")).eq("workspace_id", workspace_id).eq("id", delivery_id).execute()
        self._memory[delivery_id] = updated
        return updated

    def record_statement_reconciliation_evidence(self, workspace_id: str, data: dict) -> StatementReconciliationEvidence:
        workspace_id = require_workspace_id(workspace_id, op="record_statement_reconciliation_evidence")
        return self._put(StatementReconciliationEvidence(id=f"fin-stmt-e-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, statement_delivery_id=str(data.get("statement_delivery_id", "")), provider=str(data.get("provider", "twilio")), provider_reference=str(data.get("provider_reference", "")), attempt_number=int(data.get("attempt_number", 1)), provider_status=str(data.get("provider_status", "unknown")), canonical_status=str(data.get("canonical_status", "unknown")), terminal=bool(data.get("terminal", False)), verification_state=str(data.get("verification_state", "pending")), observed=dict(data.get("observed") or {}), evidence=dict(data.get("evidence") or {})))

    def list_statement_reconciliation_evidence(self, workspace_id: str, delivery_id: str, limit: int = 500) -> list[StatementReconciliationEvidence]:
        workspace_id = require_workspace_id(workspace_id, op="list_statement_reconciliation_evidence")
        rows = [x for x in self._memory.values() if isinstance(x, StatementReconciliationEvidence) and x.workspace_id == workspace_id and x.statement_delivery_id == delivery_id]
        if self.supabase:
            result = self.supabase.table(self.TABLES[StatementReconciliationEvidence]).select("*").eq("workspace_id", workspace_id).eq("statement_delivery_id", delivery_id).order("attempt_number", desc=True).limit(limit).execute()
            return [StatementReconciliationEvidence(**row) for row in result.data]
        return rows[:limit]

    def list_reconciliation_required_statement_deliveries(self, workspace_id: Optional[str] = None, limit: int = 100) -> list[StatementDelivery]:
        if workspace_id is not None:
            workspace_id = require_workspace_id(workspace_id, op="list_reconciliation_required_statement_deliveries")
        rows = [x for x in self._memory.values() if isinstance(x, StatementDelivery) and x.reconciliation_required and (workspace_id is None or x.workspace_id == workspace_id)]
        if self.supabase:
            query = self.supabase.table(self.TABLES[StatementDelivery]).select("*").eq("reconciliation_required", True).limit(limit)
            if workspace_id is not None: query = query.eq("workspace_id", workspace_id)
            return [StatementDelivery(**row) for row in query.execute().data]
        return rows[:limit]

    def get_outcome(self, workspace_id: str, outcome_id: str) -> Optional[FinancialOutcome]:
        return self._get(FinancialOutcome, workspace_id, outcome_id)

# ── Connector-backed payment lifecycle ────────────────────────────────────
