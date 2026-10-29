"""Commerce repository with durable Supabase and test-friendly in-memory storage."""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from core.tenant_repository import require_workspace_id

from .models import CommerceOutcome, CommercePaymentReconciliationEvidence, CommerceFulfillmentReconciliationEvidence, Customer, FulfillmentRecord, InventoryReservation, Order, OrderItem, PaymentRecord, Product
from .notification_models import CommerceOrderNotificationDelivery, CommerceOrderNotificationReconciliationEvidence

logger = logging.getLogger("kanyoza.plugins.commerce")


class CommerceRepository:
    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._products: dict[str, Product] = {}
        self._customers: dict[str, Customer] = {}
        self._orders: dict[str, Order] = {}
        self._reservations: dict[str, InventoryReservation] = {}
        self._payments: dict[str, PaymentRecord] = {}
        self._payment_reconciliation_evidence: dict[str, CommercePaymentReconciliationEvidence] = {}
        self._fulfillments: dict[str, FulfillmentRecord] = {}
        self._outcomes: dict[str, CommerceOutcome] = {}
        self._order_notification_deliveries: dict[str, CommerceOrderNotificationDelivery] = {}
        self._order_notification_evidence: dict[str, CommerceOrderNotificationReconciliationEvidence] = {}
        self._fulfillment_reconciliation_evidence: dict[str, CommerceFulfillmentReconciliationEvidence] = {}

    @staticmethod
    def _production() -> bool:
        try:
            from config.settings import settings
            import os
            mode = os.getenv("SERVICE_MODE", "").strip().lower()
            return bool(getattr(settings, "is_production", False)) and mode in {
                "full", "api", "core", "director", "meta", "condition", "workers", "monitor",
                "github", "chaos",
            }
        except Exception:
            return False

    def _require_durable_write(self, operation: str) -> None:
        if self.supabase is None and self._production():
            raise RuntimeError(f"durable commerce database is required for {operation}")

    def add_product(self, workspace_id: str, data: dict) -> Product:
        workspace_id = require_workspace_id(workspace_id, op="add_product")
        self._require_durable_write("add_product")
        product = Product(
            id=f"prd-{uuid.uuid4().hex[:12]}",
            workspace_id=workspace_id,
            name=str(data.get("name", "")).strip(),
            sku=str(data.get("sku", "")).strip(),
            price=float(data.get("price", 0)),
            currency=str(data.get("currency", "MWK")).upper(),
            inventory_quantity=int(data.get("inventory_quantity", 0)),
        )
        if not product.name or not product.sku:
            raise ValueError("product name and sku are required")
        if self.supabase:
            try:
                self.supabase.table("commerce_products").insert(product.model_dump(mode="json")).execute()
            except Exception as exc:
                logger.error("Commerce product insert failed: %s", exc)
                raise RuntimeError("commerce product persistence failed") from exc
        self._products[product.id] = product
        return product

    def get_product(self, workspace_id: str, product_id: str) -> Optional[Product]:
        workspace_id = require_workspace_id(workspace_id, op="get_product")
        if self.supabase:
            try:
                result = self.supabase.table("commerce_products").select("*").eq("workspace_id", workspace_id).eq("id", product_id).limit(1).execute()
                if result.data:
                    return Product(**result.data[0])
            except Exception as exc:
                logger.warning("Commerce product lookup failed: %s", exc)
        product = self._products.get(product_id)
        return product if product and product.workspace_id == workspace_id else None

    def add_customer(self, workspace_id: str, data: dict) -> Customer:
        workspace_id = require_workspace_id(workspace_id, op="add_customer")
        self._require_durable_write("add_customer")
        customer = Customer(
            id=f"cus-{uuid.uuid4().hex[:12]}",
            workspace_id=workspace_id,
            name=str(data.get("name", "")).strip(),
            email=data.get("email"),
            phone=data.get("phone"),
        )
        if not customer.name:
            raise ValueError("customer name is required")
        if self.supabase:
            try:
                self.supabase.table("commerce_customers").insert(customer.model_dump(mode="json")).execute()
            except Exception as exc:
                logger.error("Commerce customer insert failed: %s", exc)
                raise RuntimeError("commerce customer persistence failed") from exc
        self._customers[customer.id] = customer
        return customer

    def get_customer(self, workspace_id: str, customer_id: str) -> Optional[Customer]:
        workspace_id = require_workspace_id(workspace_id, op="get_customer")
        if self.supabase:
            try:
                result = self.supabase.table("commerce_customers").select("*").eq("workspace_id", workspace_id).eq("id", customer_id).limit(1).execute()
                if result.data:
                    return Customer(**result.data[0])
            except Exception as exc:
                logger.warning("Commerce customer lookup failed: %s", exc)
        customer = self._customers.get(customer_id)
        return customer if customer and customer.workspace_id == workspace_id else None

    def create_order(self, workspace_id: str, customer_id: str, items: list[dict]) -> Order:
        workspace_id = require_workspace_id(workspace_id, op="create_order")
        self._require_durable_write("create_order")
        customer = self.get_customer(workspace_id, customer_id)
        if customer is None:
            raise ValueError("customer not found")
        if not items:
            raise ValueError("at least one order item is required")

        order_items: list[OrderItem] = []
        total = 0.0
        currency = "MWK"
        for item in items:
            product = self.get_product(workspace_id, str(item.get("product_id", "")))
            if product is None or not product.is_active:
                raise ValueError("product not found or inactive")
            quantity = int(item.get("quantity", 0))
            if quantity <= 0:
                raise ValueError("order quantity must be positive")
            if quantity > product.inventory_quantity:
                raise ValueError(f"insufficient inventory for {product.sku}")
            line_total = round(product.price * quantity, 2)
            order_items.append(OrderItem(product_id=product.id, sku=product.sku, name=product.name, quantity=quantity, unit_price=product.price, line_total=line_total))
            total += line_total
            currency = product.currency

        order = Order(
            id=f"ord-{uuid.uuid4().hex[:12]}",
            workspace_id=workspace_id,
            customer_id=customer_id,
            items=order_items,
            total_amount=round(total, 2),
            currency=currency,
        )
        if self.supabase:
            try:
                self.supabase.table("commerce_orders").insert(order.model_dump(mode="json")).execute()
            except Exception as exc:
                logger.error("Commerce order insert failed: %s", exc)
                raise RuntimeError("commerce order persistence failed") from exc
        self._orders[order.id] = order
        return order

    def get_order(self, workspace_id: str, order_id: str) -> Optional[Order]:
        workspace_id = require_workspace_id(workspace_id, op="get_order")
        if self.supabase:
            try:
                result = self.supabase.table("commerce_orders").select("*").eq("workspace_id", workspace_id).eq("id", order_id).limit(1).execute()
                if result.data:
                    return Order(**result.data[0])
            except Exception as exc:
                logger.warning("Commerce order lookup failed: %s", exc)
        order = self._orders.get(order_id)
        return order if order and order.workspace_id == workspace_id else None


    def create_order_notification_delivery(self, workspace_id: str, data: dict) -> CommerceOrderNotificationDelivery:
        workspace_id = require_workspace_id(workspace_id, op="create_order_notification_delivery")
        self._require_durable_write("create_order_notification_delivery")
        delivery = CommerceOrderNotificationDelivery(id=f"com-msg-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, **data)
        if self.supabase:
            self.supabase.table("commerce_order_notification_deliveries").insert(delivery.model_dump(mode="json")).execute()
        self._order_notification_deliveries[delivery.id] = delivery
        return delivery

    def get_order_notification_delivery(self, workspace_id: str, delivery_id: str) -> Optional[CommerceOrderNotificationDelivery]:
        workspace_id = require_workspace_id(workspace_id, op="get_order_notification_delivery")
        if self.supabase:
            res = self.supabase.table("commerce_order_notification_deliveries").select("*").eq("workspace_id", workspace_id).eq("id", delivery_id).limit(1).execute()
            if res.data: return CommerceOrderNotificationDelivery(**res.data[0])
        item = self._order_notification_deliveries.get(delivery_id)
        return item if item and item.workspace_id == workspace_id else None

    def get_order_notification_delivery_by_idempotency_key(self, workspace_id: str, key: str) -> Optional[CommerceOrderNotificationDelivery]:
        workspace_id = require_workspace_id(workspace_id, op="get_order_notification_delivery_by_idempotency_key")
        if self.supabase:
            res = self.supabase.table("commerce_order_notification_deliveries").select("*").eq("workspace_id", workspace_id).eq("idempotency_key", key).limit(1).execute()
            if res.data: return CommerceOrderNotificationDelivery(**res.data[0])
        return next((x for x in self._order_notification_deliveries.values() if x.workspace_id == workspace_id and x.idempotency_key == key), None)

    def update_order_notification_delivery(self, workspace_id: str, delivery_id: str, **changes) -> CommerceOrderNotificationDelivery:
        delivery = self.get_order_notification_delivery(workspace_id, delivery_id)
        if delivery is None: raise ValueError("order notification delivery not found in workspace")
        updated = delivery.model_copy(update={**changes, "updated_at": datetime.now(timezone.utc)})
        self._require_durable_write("update_order_notification_delivery")
        if self.supabase:
            self.supabase.table("commerce_order_notification_deliveries").update(updated.model_dump(mode="json")).eq("workspace_id", workspace_id).eq("id", delivery_id).execute()
        self._order_notification_deliveries[delivery.id] = updated
        return updated

    def list_pending_order_notification_deliveries(self, workspace_id: str | None = None, limit: int = 100):
        if workspace_id is not None:
            workspace_id = require_workspace_id(workspace_id, op="list_pending_order_notification_deliveries")
        if self.supabase:
            q = self.supabase.table("commerce_order_notification_deliveries").select("*").eq("reconciliation_required", True).order("created_at").limit(limit)
            if workspace_id is not None: q = q.eq("workspace_id", workspace_id)
            res = q.execute()
            return [CommerceOrderNotificationDelivery(**x) for x in (res.data or [])]
        return [x for x in self._order_notification_deliveries.values() if x.reconciliation_required and (workspace_id is None or x.workspace_id == workspace_id)][:limit]

    def record_order_notification_reconciliation_evidence(self, workspace_id: str, data: dict) -> CommerceOrderNotificationReconciliationEvidence:
        workspace_id = require_workspace_id(workspace_id, op="record_order_notification_reconciliation_evidence")
        row = CommerceOrderNotificationReconciliationEvidence(id=f"com-msg-recon-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, **data)
        self._require_durable_write("record_order_notification_reconciliation_evidence")
        if self.supabase: self.supabase.table("commerce_order_notification_reconciliation_evidence").insert(row.model_dump(mode="json")).execute()
        self._order_notification_evidence[row.id] = row
        return row

    def list_order_notification_reconciliation_evidence(self, workspace_id: str, delivery_id: str, limit: int = 500):
        workspace_id = require_workspace_id(workspace_id, op="list_order_notification_reconciliation_evidence")
        if self.supabase:
            res = self.supabase.table("commerce_order_notification_reconciliation_evidence").select("*").eq("workspace_id", workspace_id).eq("delivery_id", delivery_id).order("attempt_number", desc=True).limit(limit).execute()
            return [CommerceOrderNotificationReconciliationEvidence(**x) for x in (res.data or [])]
        rows = [x for x in self._order_notification_evidence.values() if x.workspace_id == workspace_id and x.delivery_id == delivery_id]
        return sorted(rows, key=lambda x: x.attempt_number, reverse=True)[:limit]

    def reserve_inventory(self, workspace_id: str, order_id: str) -> list[InventoryReservation]:
        workspace_id = require_workspace_id(workspace_id, op="reserve_inventory")
        self._require_durable_write("reserve_inventory")
        order = self.get_order(workspace_id, order_id)
        if order is None:
            raise ValueError("order not found")
        existing = [r for r in self._reservations.values() if r.workspace_id == workspace_id and r.order_id == order_id and r.status == "reserved"]
        if existing:
            return existing
        reservations: list[InventoryReservation] = []
        for item in order.items:
            product = self.get_product(workspace_id, item.product_id)
            if product is None or product.inventory_quantity < item.quantity:
                raise ValueError(f"insufficient inventory for {item.sku}")
            product = product.model_copy(update={"inventory_quantity": product.inventory_quantity - item.quantity})
            self._products[product.id] = product
            reservation = InventoryReservation(id=f"res-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, order_id=order_id, product_id=item.product_id, quantity=item.quantity)
            if self.supabase:
                try:
                    self.supabase.table("commerce_inventory_reservations").insert(reservation.model_dump(mode="json")).execute()
                    self.supabase.table("commerce_products").update({"inventory_quantity": product.inventory_quantity}).eq("workspace_id", workspace_id).eq("id", product.id).execute()
                except Exception as exc:
                    logger.error("Commerce inventory reservation failed: %s", exc)
                    raise RuntimeError("commerce inventory reservation persistence failed") from exc
            self._reservations[reservation.id] = reservation
            reservations.append(reservation)
        return reservations

    def get_payment(self, workspace_id: str, payment_id: str) -> Optional[PaymentRecord]:
        workspace_id = require_workspace_id(workspace_id, op="get_payment")
        if self.supabase:
            res = self.supabase.table("commerce_payments").select("*").eq("workspace_id", workspace_id).eq("id", payment_id).limit(1).execute()
            if res.data: return PaymentRecord(**res.data[0])
        payment = self._payments.get(payment_id)
        return payment if payment and payment.workspace_id == workspace_id else None

    def get_payment_for_order(self, workspace_id: str, order_id: str) -> Optional[PaymentRecord]:
        workspace_id = require_workspace_id(workspace_id, op="get_payment_for_order")
        if self.supabase:
            res = self.supabase.table("commerce_payments").select("*").eq("workspace_id", workspace_id).eq("order_id", order_id).order("created_at", desc=True).limit(1).execute()
            if res.data: return PaymentRecord(**res.data[0])
        rows = [x for x in self._payments.values() if x.workspace_id == workspace_id and x.order_id == order_id]
        return max(rows, key=lambda x: x.created_at) if rows else None

    def update_payment(self, workspace_id: str, payment_id: str, **changes) -> PaymentRecord:
        payment = self._payments.get(payment_id)
        if payment is None or payment.workspace_id != require_workspace_id(workspace_id, op="update_payment"):
            raise ValueError("payment not found in workspace")
        updated = payment.model_copy(update={**changes, "updated_at": datetime.now(timezone.utc)})
        self._require_durable_write("update_payment")
        if self.supabase:
            self.supabase.table("commerce_payments").update(updated.model_dump(mode="json")).eq("workspace_id", workspace_id).eq("id", payment_id).execute()
        self._payments[payment_id] = updated
        return updated

    def record_payment_reconciliation_evidence(self, workspace_id: str, data: dict) -> CommercePaymentReconciliationEvidence:
        workspace_id = require_workspace_id(workspace_id, op="record_payment_reconciliation_evidence")
        row = CommercePaymentReconciliationEvidence(id=f"com-pay-recon-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, **data)
        self._require_durable_write("record_payment_reconciliation_evidence")
        if self.supabase: self.supabase.table("commerce_payment_reconciliation_evidence").insert(row.model_dump(mode="json")).execute()
        self._payment_reconciliation_evidence[row.id] = row
        return row

    def list_payment_reconciliation_evidence(self, workspace_id: str, payment_id: str, limit: int = 500):
        workspace_id = require_workspace_id(workspace_id, op="list_payment_reconciliation_evidence")
        if self.supabase:
            res = self.supabase.table("commerce_payment_reconciliation_evidence").select("*").eq("workspace_id", workspace_id).eq("payment_id", payment_id).order("attempt_number", desc=True).limit(limit).execute()
            return [CommercePaymentReconciliationEvidence(**x) for x in (res.data or [])]
        rows = [x for x in self._payment_reconciliation_evidence.values() if x.workspace_id == workspace_id and x.payment_id == payment_id]
        return sorted(rows, key=lambda x: x.attempt_number, reverse=True)[:limit]

    def record_fulfillment_reconciliation_evidence(self, workspace_id: str, data: dict) -> CommerceFulfillmentReconciliationEvidence:
        workspace_id = require_workspace_id(workspace_id, op="record_fulfillment_reconciliation_evidence")
        self._require_durable_write("record_fulfillment_reconciliation_evidence")
        row = CommerceFulfillmentReconciliationEvidence(id=f"com-ful-recon-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, **data)
        if self.supabase: self.supabase.table("commerce_fulfillment_reconciliation_evidence").insert(row.model_dump(mode="json")).execute()
        self._fulfillment_reconciliation_evidence[row.id] = row
        return row

    def list_fulfillment_reconciliation_evidence(self, workspace_id: str, order_id: str, limit: int = 500):
        workspace_id = require_workspace_id(workspace_id, op="list_fulfillment_reconciliation_evidence")
        if self.supabase:
            res = self.supabase.table("commerce_fulfillment_reconciliation_evidence").select("*").eq("workspace_id", workspace_id).eq("order_id", order_id).order("attempt_number", desc=True).limit(limit).execute()
            return [CommerceFulfillmentReconciliationEvidence(**x) for x in (res.data or [])]
        rows = [x for x in self._fulfillment_reconciliation_evidence.values() if x.workspace_id == workspace_id and x.order_id == order_id]
        return sorted(rows, key=lambda x: x.attempt_number, reverse=True)[:limit]

    def record_payment(self, workspace_id: str, data: dict) -> PaymentRecord:
        workspace_id = require_workspace_id(workspace_id, op="record_payment")
        self._require_durable_write("record_payment")
        order = self.get_order(workspace_id, str(data.get("order_id", "")))
        if order is None:
            raise ValueError("order not found")
        status = str(data.get("status", "pending")).lower()
        if status not in {"pending", "authorized", "captured", "failed", "refunded"}:
            raise ValueError("unsupported payment status")
        payment = PaymentRecord(id=f"pay-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, order_id=order.id, amount=float(data.get("amount", order.total_amount)), currency=str(data.get("currency", order.currency)).upper(), status=status, provider_reference=data.get("provider_reference"))
        if payment.amount > order.total_amount:
            raise ValueError("payment amount exceeds order total")
        if self.supabase:
            try:
                self.supabase.table("commerce_payments").insert(payment.model_dump(mode="json")).execute()
                self.supabase.table("commerce_orders").update({"payment_status": status}).eq("workspace_id", workspace_id).eq("id", order.id).execute()
            except Exception as exc:
                logger.error("Commerce payment record failed: %s", exc)
                raise RuntimeError("commerce payment persistence failed") from exc
        self._payments[payment.id] = payment
        self._orders[order.id] = order.model_copy(update={"payment_status": status, "updated_at": datetime.now(timezone.utc)})
        return payment

    def record_fulfillment(self, workspace_id: str, data: dict) -> FulfillmentRecord:
        workspace_id = require_workspace_id(workspace_id, op="record_fulfillment")
        self._require_durable_write("record_fulfillment")
        order = self.get_order(workspace_id, str(data.get("order_id", "")))
        if order is None:
            raise ValueError("order not found")
        status = str(data.get("status", "requested")).lower()
        if status not in {"requested", "packed", "shipped", "delivered", "failed", "cancelled"}:
            raise ValueError("unsupported fulfillment status")
        fulfillment = FulfillmentRecord(id=f"ful-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, order_id=order.id, status=status, carrier=data.get("carrier"), tracking_reference=data.get("tracking_reference"))
        if self.supabase:
            try:
                self.supabase.table("commerce_fulfillments").insert(fulfillment.model_dump(mode="json")).execute()
                self.supabase.table("commerce_orders").update({"fulfillment_status": status}).eq("workspace_id", workspace_id).eq("id", order.id).execute()
            except Exception as exc:
                logger.error("Commerce fulfillment record failed: %s", exc)
                raise RuntimeError("commerce fulfillment persistence failed") from exc
        self._fulfillments[fulfillment.id] = fulfillment
        self._orders[order.id] = order.model_copy(update={"fulfillment_status": status, "updated_at": datetime.now(timezone.utc)})
        return fulfillment

    def record_outcome(self, workspace_id: str, data: dict) -> CommerceOutcome:
        workspace_id = require_workspace_id(workspace_id, op="record_commerce_outcome")
        self._require_durable_write("record_commerce_outcome")
        order_id = data.get("order_id")
        if order_id and self.get_order(workspace_id, str(order_id)) is None:
            raise ValueError("order not found")
        outcome = CommerceOutcome(id=f"cout-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, order_id=str(order_id) if order_id else None, metric=str(data.get("metric", "")).strip(), value=float(data.get("value", 0)), source=str(data.get("source", "commerce")).strip(), evidence=data.get("evidence") or {})
        if not outcome.metric:
            raise ValueError("metric is required")
        if self.supabase:
            try:
                self.supabase.table("commerce_outcomes").insert(outcome.model_dump(mode="json")).execute()
            except Exception as exc:
                logger.error("Commerce outcome record failed: %s", exc)
                raise RuntimeError("commerce outcome persistence failed") from exc
        self._outcomes[outcome.id] = outcome
        return outcome

    def update_order_status(self, workspace_id: str, order_id: str, status: str) -> Optional[Order]:
        workspace_id = require_workspace_id(workspace_id, op="update_order_status")
        self._require_durable_write("update_order_status")
        allowed = {"pending", "confirmed", "cancelled", "fulfilled"}
        if status not in allowed:
            raise ValueError(f"unsupported order status: {status}")
        if self.supabase:
            try:
                result = self.supabase.table("commerce_orders").update({"status": status, "updated_at": datetime.now(timezone.utc).isoformat()}).eq("workspace_id", workspace_id).eq("id", order_id).select("*").limit(1).execute()
                if result.data:
                    return Order(**result.data[0])
            except Exception as exc:
                logger.error("Commerce order update failed: %s", exc)
                raise RuntimeError("commerce order persistence failed") from exc
        order = self._orders.get(order_id)
        if order and order.workspace_id == workspace_id:
            order.status = status
            order.updated_at = datetime.now(timezone.utc)
            return order
        return None
