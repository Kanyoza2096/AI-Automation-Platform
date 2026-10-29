"""System-level vertical slice certification.

This test intentionally composes existing platform boundaries instead of adding
another execution implementation:

intelligence decision -> governance -> sector workflow -> Action Kernel ->
ExternalActionExecutor -> ExecutionLedger -> provider receipt -> verification.

The synthetic sector is test-only so the certification cannot accidentally
become a production feature.
"""

import asyncio

from core.decision_gate import DecisionSource, DecisionGate
from plugins.industries.base import IndustryPlugin
from services.action_kernel import ActionContract, ActionKernel


class SyntheticWarehousePlugin(IndustryPlugin):
    pack_id = "warehouse_ops_vertical_slice"
    pack_version = "1.0"
    display_name = "Warehouse Operations Vertical Slice"
    capabilities = ("inventory", "dispatch", "notifications")
    resources = ("inventory_items", "shipments")
    policies = ("warehouse.workspace_access", "warehouse.inventory_access")
    workflows = ("dispatch_shipment",)
    events = ("shipment.created", "shipment.dispatched")
    external_actions = ("warehouse.notify_dispatch",)

    def get_commands(self):
        return [{"intent": "dispatch_shipment"}]

    def execute(self, command, params):
        return {"ok": True, "command": command}

    def get_webhooks(self):
        return []


def _build_slice():
    plugin = SyntheticWarehousePlugin(None, None)
    gate = DecisionGate()
    kernel = ActionKernel(None, allow_in_memory=True)
    calls = []

    def connector_operation(params, intent):
        calls.append({"shipment_id": params["shipment_id"], "decision_id": intent.decision_id})
        return {
            "ok": True,
            "object_id": f"shipment-receipt-{params['shipment_id']}",
            "request_id": "req-warehouse-001",
        }

    kernel.register(
        ActionContract(
            "warehouse_notify_dispatch",
            risk_level="low",
            required_parameters=("shipment_id", "message"),
            handler=connector_operation,
        )
    )
    return plugin, gate, kernel, calls


def test_full_vertical_slice_crosses_all_authoritative_boundaries():
    plugin, gate, kernel, calls = _build_slice()
    manifest = plugin.get_manifest()

    # Intelligence/governance boundary: a decision is issued before execution.
    decision = gate.issue(
        source=DecisionSource.DIRECTOR_AUTO,
        workspace_id="ws-warehouse",
        action_name="warehouse_notify_dispatch",
        approved=True,
        risk_level="low",
        reason="Shipment dispatch notification authorized",
    )

    # Sector workflow boundary: the sector only supplies domain parameters and
    # the declared external action; it does not create an executor or ledger.
    result = asyncio.run(
        kernel.execute(
            workspace_id="ws-warehouse",
            action_type="warehouse_notify_dispatch",
            parameters={
                "shipment_id": "SHP-42",
                "message": "Shipment dispatched",
            },
            risk_level="low",
            decision_id=decision.decision_id,
            correlation_id=decision.correlation_id,
            idempotency_key="shipment-dispatch-SHP-42",
            source="warehouse.dispatch_shipment",
            metadata={"workflow": "dispatch_shipment", "pack_id": manifest.pack_id},
        )
    )

    assert result["status"] == "succeeded"
    assert result["execution_id"].startswith("exe_")
    assert result["intent_id"].startswith("act_")
    assert result["output"]["object_id"] == "shipment-receipt-SHP-42"
    assert calls == [{"shipment_id": "SHP-42", "decision_id": decision.decision_id}]

    # Verification: the decision remains traceable to the external outcome.
    recorded = kernel.ledger._memory["shipment-dispatch-SHP-42"]
    assert recorded.execution_id == result["execution_id"]
    assert recorded.status == "succeeded"
    assert recorded.provider_object_id == "shipment-receipt-SHP-42"

    outcome = gate.record_outcome(
        decision_id=decision.decision_id,
        outcome="success",
        source="execution_ledger",
        metadata={"execution_id": result["execution_id"]},
        workspace_id="ws-warehouse",
    )
    assert outcome is not None
    assert outcome.outcome == "success"
    assert outcome.outcome_source == "execution_ledger"


def test_governance_blocks_vertical_slice_when_decision_is_missing():
    _plugin, _gate, _kernel, calls = _build_slice()
    kernel = ActionKernel(None, allow_in_memory=True)
    kernel.register(
        ActionContract(
            "warehouse_notify_dispatch",
            risk_level="high",
            required_parameters=("shipment_id", "message"),
            handler=lambda params, intent: calls.append(params["shipment_id"]) or {
                "ok": True, "object_id": "must-not-execute"
            },
        )
    )

    # A high-risk action with no decision must stop before the provider.
    result = asyncio.run(
        kernel.execute(
            workspace_id="ws-warehouse",
            action_type="warehouse_notify_dispatch",
            parameters={"shipment_id": "SHP-43", "message": "Dispatch"},
            risk_level="high",
            idempotency_key="shipment-dispatch-SHP-43",
        )
    )

    assert result["status"] == "governance_required"
    assert calls == []
    assert result["execution_id"] == ""


def test_vertical_slice_preserves_uncertain_external_reality_for_reconciliation():
    plugin, gate, kernel, calls = _build_slice()

    decision = gate.issue(
        source=DecisionSource.DIRECTOR_AUTO,
        workspace_id="ws-warehouse",
        action_name="warehouse_notify_dispatch",
        approved=True,
        risk_level="low",
        reason="Dispatch notification authorized",
    )

    # Replace only the connector handler with an ambiguous external receipt.
    kernel._contracts["warehouse_notify_dispatch"] = ActionContract(
        "warehouse_notify_dispatch",
        risk_level="low",
        required_parameters=("shipment_id", "message"),
        handler=lambda params, intent: calls.append(params["shipment_id"]) or {"ok": True},
    )

    result = asyncio.run(
        kernel.execute(
            workspace_id="ws-warehouse",
            action_type="warehouse_notify_dispatch",
            parameters={"shipment_id": "SHP-44", "message": "Dispatch"},
            risk_level="low",
            decision_id=decision.decision_id,
            idempotency_key="shipment-dispatch-SHP-44",
        )
    )

    assert result["status"] == "uncertain"
    assert result["reconciliation_required"] is True
    assert calls == ["SHP-44"]
