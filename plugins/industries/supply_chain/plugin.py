"""Supply Chain / Logistics sector pack."""
from plugins.industries.base import IndustryPlugin


class SupplyChainPlugin(IndustryPlugin):
    pack_id = "supply_chain"
    pack_version = "1.1"
    display_name = "Supply Chain & Logistics"
    capabilities = ("shipment_management", "inventory_operations", "purchase_orders", "carrier_governance", "logistics_remediation", "exception_management", "logistics_outcomes")
    resources = ("shipments", "warehouse_inventory", "purchase_orders", "warehouses", "carriers", "shipment_exceptions", "logistics_outcomes")
    policies = ("supply_chain.workspace_access", "supply_chain.shipment_access", "supply_chain.inventory_authorization", "supply_chain.carrier_authorization", "supply_chain.exception_integrity", "supply_chain.outcome_integrity")
    workflows = ("shipment_lifecycle", "inventory_replenishment", "purchase_order_lifecycle", "carrier_dispatch", "shipment_exception_management", "logistics_outcome_loop")
    events = ("shipment.created", "shipment.updated", "inventory.updated", "purchase_order.created", "carrier.booking_requested", "shipment.exception_recorded", "logistics.outcome.observed")
    external_actions = ("supply_chain.dispatch_shipment", "supply_chain.request_carrier_booking", "supply_chain.record_exception", "supply_chain.record_outcome")

    def __init__(self, supabase_client=None, redis_client=None):
        super().__init__(supabase_client, redis_client)
        from .repository import SupplyChainRepository
        from .commands import SupplyChainCommands
        self.repo = SupplyChainRepository(supabase_client)
        self.commands_handler = SupplyChainCommands(self.repo, redis_client)

    def get_commands(self):
        return self.commands_handler.list_all()

    def execute(self, command, params, workspace_id="default"):
        workspace_id = self.resolve_workspace(workspace_id)
        safe_params = {k: v for k, v in (params or {}).items() if k != "workspace_id"}
        return self.commands_handler.handle(command, safe_params, workspace_id)

    def get_webhooks(self):
        return [
            {"path": "/webhooks/supply-chain/shipment", "method": "POST", "handler": "handle_shipment_webhook", "description": "External shipment lifecycle event"},
            {"path": "/webhooks/supply-chain/carrier", "method": "POST", "handler": "handle_carrier_webhook", "description": "Carrier status/reconciliation event"},
        ]
