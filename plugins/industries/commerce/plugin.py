"""Commerce sector pack — catalog, customers, orders and governed commerce actions."""

from plugins.industries.base import IndustryPlugin


class CommercePlugin(IndustryPlugin):
    pack_id = "commerce"
    pack_version = "1.1"
    display_name = "Commerce & E-commerce"
    capabilities = ("catalog", "customers", "orders", "inventory", "payments", "fulfillment", "outcomes")
    resources = ("products", "customers", "orders", "order_items", "inventory_reservations", "payments", "fulfillments", "commerce_outcomes")
    policies = ("commerce.workspace_access", "commerce.order_access", "commerce.inventory_authorization", "commerce.payment_authorization", "commerce.fulfillment_authorization", "commerce.outcome_integrity")
    workflows = ("order_lifecycle", "inventory_reservation", "payment_capture", "order_fulfillment", "commerce_outcome_loop")
    events = ("product.created", "customer.created", "order.created", "order.updated", "inventory.reserved", "payment.requested", "payment.recorded", "fulfillment.requested", "fulfillment.recorded", "commerce.outcome.observed")
    external_actions = ("commerce.capture_payment", "commerce.fulfill_order", "commerce.send_order_notification", "commerce.record_payment", "commerce.record_fulfillment", "commerce.record_outcome")

    def __init__(self, supabase_client=None, redis_client=None):
        super().__init__(supabase_client, redis_client)
        from .repository import CommerceRepository
        from .commands import CommerceCommands
        self.repo = CommerceRepository(supabase_client)
        self.commands_handler = CommerceCommands(self.repo, redis_client)

    def get_commands(self) -> list[dict]:
        return self.commands_handler.list_all()

    def execute(self, command: str, params: dict, workspace_id: str = "default") -> dict:
        workspace_id = self.resolve_workspace(workspace_id)
        safe_params = {k: v for k, v in (params or {}).items() if k != "workspace_id"}
        return self.commands_handler.handle(command, safe_params, workspace_id)

    def get_webhooks(self) -> list[dict]:
        return [
            {"path": "/webhooks/commerce/order", "method": "POST", "handler": "handle_order_webhook", "description": "Order lifecycle event from an external commerce system"},
            {"path": "/webhooks/commerce/payment", "method": "POST", "handler": "handle_payment_webhook", "description": "Payment status event from an external payment provider"},
        ]
