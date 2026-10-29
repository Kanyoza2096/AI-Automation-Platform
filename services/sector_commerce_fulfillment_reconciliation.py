"""Authoritative Shopify fulfillment reconciliation for Commerce."""
from __future__ import annotations
from datetime import datetime, timezone

class CommerceFulfillmentReconciliationService:
    def __init__(self, repository, shopify_adapter): self.repository=repository; self.shopify=shopify_adapter
    def reconcile(self, *, workspace_id, order_id, fulfillment_id):
        order=self.repository.get_order(workspace_id, order_id)
        if order is None: raise ValueError("order not found in workspace")
        prior=self.repository.list_fulfillment_reconciliation_evidence(workspace_id, order_id, limit=1)
        attempt=(prior[0].attempt_number + 1) if prior else 1
        observed=self.shopify.reconcile_fulfillment(fulfillment_id)
        status=observed.get("status")
        terminal=status in {"succeeded","failed"}
        verification_state="verified_provider_terminal" if terminal else "provider_observed_nonterminal"
        if status == "succeeded":
            self.repository.record_fulfillment(workspace_id,{"order_id":order_id,"status":"shipped","carrier":None,"tracking_reference":fulfillment_id})
        elif status == "failed":
            self.repository.record_fulfillment(workspace_id,{"order_id":order_id,"status":"failed","carrier":None,"tracking_reference":fulfillment_id})
        self.repository.record_fulfillment_reconciliation_evidence(workspace_id,{"order_id":order_id,"provider":"shopify","provider_reference":fulfillment_id,"attempt_number":attempt,"provider_status":str(observed.get("provider_status") or "unknown"),"canonical_status":status,"terminal":terminal,"verification_state":verification_state,"observed":observed.get("observed") or {},"evidence":{"display_status":observed.get("display_status"),"reconciled_at":datetime.now(timezone.utc).isoformat()}})
        return {"status":status,"terminal":terminal,"reconciliation_required":not terminal,"provider_reference":fulfillment_id,"observed":observed,"attempt_number":attempt}
