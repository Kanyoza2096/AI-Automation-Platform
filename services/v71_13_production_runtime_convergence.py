"""V71.13 — Production Runtime Convergence.

Composition root for the Business Operating System + Governed AI Workforce.
This module does not create a second execution engine. It resolves the existing
BusinessAutomationEngine/Action Kernel path and composes the V70/V71 control
planes per workspace.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
import hashlib
import json
import logging
import os

logger = logging.getLogger("kanyoza.v71.13")


@dataclass(frozen=True)
class RuntimeLayerStatus:
    name: str
    version: str
    present: bool
    canonical: bool = True
    notes: str = ""


class ProductionRuntimeConvergence:
    VERSION = "v71.13"

    def __init__(self, *, container: Any, db: Any = None):
        if container is None:
            raise ValueError("service container is required")
        self.container = container
        self.db = db
        self._workspaces: dict[str, dict[str, Any]] = {}
        self._boot_verified = False
        self._legacy_runtime_observed = False

    def _resolve_business_engine(self):
        # BusinessAutomationEngine is intentionally imported lazily so startup
        # remains compatible with service modes that omit business services.
        from services.business_automation import BusinessAutomationEngine
        engine = self.container.try_resolve(BusinessAutomationEngine)
        if engine is None:
            engine = BusinessAutomationEngine(supabase_client=self.db)
        return engine

    def _db_client(self):
        if self.db is not None:
            return self.db
        try:
            from services.supabase import supabase_service
            return supabase_service.service_client or supabase_service.client
        except Exception:
            return None

    def verify_boot(self) -> dict[str, Any]:
        """Verify that the canonical V70/V71 composition is importable.

        This is a wiring assertion, not live-provider certification.
        """
        engine = self._resolve_business_engine()
        checks: dict[str, bool] = {}
        modules = {
            "business_operating_layer": "services.v70_business_operating_layer",
            "canonical_runtime": "services.v70_4_canonical_runtime",
            "customer_sales": "services.v70_2_customer_sales_operating_loop",
            "content_marketing": "services.v70_6_content_marketing_operating_loop",
            "sales_pipeline": "services.v70_7_sales_pipeline_opportunity_operating_system",
            "customer_lifecycle": "services.v70_8_customer_lifecycle_retention",
            "internal_operations": "services.v70_9_internal_business_operations",
            "business_command": "services.v70_10_business_command_planning",
            "cross_domain": "services.v70_11_cross_domain_business_orchestration",
            "agent_identity": "services.v71_1_agent_identity",
            "agent_permissions": "services.v71_2_agent_capability_permissions",
            "agent_memory": "services.v71_3_agent_memory",
            "agent_planning": "services.v71_4_agent_planning",
            "agent_evaluation": "services.v71_5_agent_evaluation",
            "agent_budgets": "services.v71_6_agent_budgets",
            "human_takeover": "services.v71_7_human_takeover",
            "multi_agent": "services.v71_8_multi_agent_coordination",
            "bounded_autonomy": "services.v71_9_bounded_autonomous_operations",
            "durable_runtime": "services.v71_10_durable_workforce_runtime",
            "observability_control": "services.v71_11_workforce_observability_control",
        }
        for key, module in modules.items():
            try:
                __import__(module)
                checks[key] = True
            except Exception as exc:
                logger.error("V71.13 layer import failed [%s]: %s", key, exc)
                checks[key] = False

        canonical = all(checks.values()) and callable(getattr(engine, "run_event", None))
        self._boot_verified = canonical
        status = {
            "version": self.VERSION,
            "boot_verified": canonical,
            "business_engine": type(engine).__name__,
            "checks": checks,
            "execution_law": "BusinessAutomation -> Action Kernel -> durable outbox -> connector/provider",
            "parallel_execution_plane": False,
            "live_provider_certification": False,
        }
        logger.info("V71.13 production runtime convergence verified=%s", canonical)
        return status

    def workspace_runtime(self, workspace_id: str) -> dict[str, Any]:
        ws = str(workspace_id or "").strip()
        if not ws:
            raise ValueError("workspace_id is required")
        existing = self._workspaces.get(ws)
        if existing:
            return existing

        from services.v70_business_operating_layer import BusinessOperatingLayer
        from services.v71_1_agent_identity import AgentIdentityRegistry
        from services.v71_2_agent_capability_permissions import AgentCapabilityPermissionRegistry
        from services.v71_3_agent_memory import AgentMemoryRegistry
        from services.v71_4_agent_planning import AgentPlanningRegistry
        from services.v71_5_agent_evaluation import AgentEvaluationRegistry
        from services.v71_6_agent_budgets import AgentBudgetRegistry
        from services.v71_7_human_takeover import HumanTakeoverRegistry
        from services.v71_8_multi_agent_coordination import MultiAgentCoordinationRegistry
        from services.v71_9_bounded_autonomous_operations import BoundedAutonomousOperationsRegistry
        from services.v71_10_durable_workforce_runtime import DurableAutonomousWorkforceRuntime
        from services.v71_11_workforce_observability_control import WorkforceObservabilityControlPlane

        engine = self._resolve_business_engine()
        operating = BusinessOperatingLayer(ws, engine=engine)
        identity = AgentIdentityRegistry(ws)
        permissions = AgentCapabilityPermissionRegistry(ws, identity)
        memory = AgentMemoryRegistry(ws, identity)
        planning = AgentPlanningRegistry(ws, identity, permissions, memory)
        evaluation = AgentEvaluationRegistry(ws, identity, planning)
        budgets = AgentBudgetRegistry(ws, identity)
        takeover = HumanTakeoverRegistry(ws, identity)
        coordination = MultiAgentCoordinationRegistry(
            ws, identity, permissions, planning, memory, evaluation, budgets, takeover
        )
        operations = BoundedAutonomousOperationsRegistry(
            ws, identity, permissions, planning, evaluation, budgets, takeover, coordination, db=self._db_client()
        )
        runtime = DurableAutonomousWorkforceRuntime(
            ws, operations, db=self._db_client(), allow_in_memory=False
        )
        control = WorkforceObservabilityControlPlane(ws, operations, db=self._db_client(), allow_in_memory=False)

        value = {
            "workspace_id": ws,
            "business_operating_layer": operating,
            "agent_identity": identity,
            "agent_permissions": permissions,
            "agent_memory": memory,
            "agent_planning": planning,
            "agent_evaluation": evaluation,
            "agent_budgets": budgets,
            "human_takeover": takeover,
            "multi_agent_coordination": coordination,
            "bounded_autonomy": operations,
            "durable_runtime": runtime,
            "observability_control": control,
        }
        # V71.18: rehydrate the existing control plane from authoritative durable tables
        # before a recovered autonomous run can pass governance. This is restore-only;
        # no authority or provider execution is created here.
        try:
            from services.v71_18_workforce_control_plane_persistence import WorkforceControlPlanePersistence
            durable_db = self._db_client()
            # Some isolated composition tests provide a sentinel DB object. Do not
            # pretend that it is durable; production execution remains fail-closed
            # in V71.10 when a real durable store is unavailable.
            if durable_db is None or not callable(getattr(durable_db, "table", None)):
                value["control_plane_persistence"] = None
                value["control_plane_persistence_report"] = None
            else:
                persistence = WorkforceControlPlanePersistence(workspace_id=ws, db=durable_db)
                report = persistence.hydrate(value)
                value["control_plane_persistence"] = persistence
                value["control_plane_persistence_report"] = report
                if not report.restart_safe:
                    raise RuntimeError("workforce control-plane integrity failure after hydration")
        except Exception:
            # Production must fail closed: a workspace with durable runs cannot
            # silently fall back to empty in-memory control-plane registries.
            raise
        self._workspaces[ws] = value
        return value

    def status(self) -> dict[str, Any]:
        layers = [
            RuntimeLayerStatus("Business Operating Layer", "v70.1-v70.12", True, True),
            RuntimeLayerStatus("Canonical Runtime", "v70.4", True, True),
            RuntimeLayerStatus("Governed AI Workforce", "v71.1-v71.12", True, True),
            RuntimeLayerStatus("Action Kernel", "existing", True, True),
            RuntimeLayerStatus("Durable Outbox", "existing", True, True),
            RuntimeLayerStatus("Legacy AutonomousLoop", "legacy", True, False, "must not become a parallel authority/executor"),
        ]
        return {
            "version": self.VERSION,
            "boot_verified": self._boot_verified,
            "workspaces_composed": len(self._workspaces),
            "layers": [x.__dict__ for x in layers],
            "canonical_path": "business objective -> operating layer -> workforce -> governance -> Action Kernel -> durable outbox -> connector -> provider -> reconciliation -> outcome",
            "legacy_runtime": "preserved infrastructure; convergence adapter/fence required before it may initiate governed business execution",
            "certification": "NOT_PROVEN",
        }


def install_production_runtime(app: Any, container: Any) -> ProductionRuntimeConvergence:
    runtime = ProductionRuntimeConvergence(container=container)
    app.state.production_runtime = runtime
    runtime.verify_boot()
    return runtime
