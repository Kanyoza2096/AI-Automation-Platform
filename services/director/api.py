"""
KANYOZA DIRECTOR — FastAPI Surface (v5.5)

v5.5 CHANGES:
  - /proposals list and detail endpoints
  - /signals list endpoint
  - /proposals/{id}/approve and /reject endpoints
  - /detectors/run endpoint for manual detector triggering
  - /learning/verification endpoint showing verified vs ineffective outcomes
  - /learning/targets endpoint showing per-target learning state
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import logging
import os
from typing import Dict, Optional

# FastAPI's dependency injection recognizes starlette.requests.Request.
# Importing it at module scope (not inside create_app) ensures every
# endpoint using `request: Request` gets the actual request object
# injected, not a required query parameter. FastAPI re-exports
# starlette's Request, so this is the same class — but some FastAPI/
# Starlette version combinations fail to recognize it when imported
# inside a factory function. Module-scope import fixes the 422
# "query.request is required" error that appeared on every
# /director/* endpoint with a Request parameter.
from starlette.requests import Request
from pydantic import BaseModel, Field

from .utils import safe
from .models import (
    Authority,
    Briefing,
    Capability,
    Health,
    ProposalStatus,
    Risk,
    Sector,
    Status,
    Task,
    AsyncCircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
    SupabaseErrorClassifier,
)
from .analysis import (
    AdaptiveThresholds,
    CascadingFailurePredictor,
    CorrelatedRecovery,
    FunctionObserver,
    ImpactAssessor,
    LearningLoop,
    Policy,
    RootCauseAnalyzer,
    SystemTopology,
    ENGINE_REGISTRY,
)
from .core import Director, auto_discover_engines
from .planning import StrategicPlannerUnavailable
log = logging.getLogger("kanyoza.director")

class StrategicMissionAdmissionBody(BaseModel):
    idempotency_key: str = Field(..., min_length=8, max_length=128)
    step_parameters: dict[str, dict] = Field(..., min_length=1, max_length=12)
class StrategicObjectiveBody(BaseModel):
    objective_id: Optional[str] = Field(default=None, min_length=1, max_length=100)
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1, max_length=4000)
    success_criteria: list[str] = Field(..., min_length=1, max_length=20)
    constraints: list[str] = Field(default_factory=list, max_length=20)



try:
    from opentelemetry import trace
    tracer = trace.get_tracer("kanyoza.director", "5.5.0")
    HAS_TRACING = True
except ImportError:
    tracer = None
    HAS_TRACING = False

try:
    from council.integration import get_council
    HAS_COUNCIL = True
except ImportError:
    HAS_COUNCIL = False

try:
    from core.unified_bus import get_unified_bus
    HAS_EVENT_BUS = True
except ImportError:
    HAS_EVENT_BUS = False


def create_app(director: Director):
    """Create FastAPI app with full authentication."""
    from fastapi import FastAPI, HTTPException
    from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
    from pydantic import BaseModel, Field
    from dataclasses import asdict

    app = FastAPI(title="Kanyoza Director", version=director.VERSION)
    security = HTTPBearer(auto_error=False)

    def _authenticate(credentials, request) -> bool:
        if request is None:
            return False
        context = getattr(request.state, "request_context", None)
        identity = getattr(request.state, "auth_identity", None)
        if context is not None and getattr(context, "workspace_id", None):
            # A workspace context is not proof of authentication by itself.
            # Canonical middleware binds both objects after validating the
            # credential; fail closed if the trusted identity is absent/invalid.
            return bool(identity is not None and getattr(identity, "valid", False))

        master_token = (os.getenv("MASTER_API_TOKEN", "") or "").strip()
        service_token = (os.getenv("PYTHON_SERVICE_TOKEN", "") or "").strip()
        accepted = [t for t in (master_token, service_token) if t]
        if not accepted:
            return False

        token = ""
        if credentials:
            token = credentials.credentials
        if not token:
            token = (
                request.headers.get("X-API-Key", "")
                or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            )
        if not token or not isinstance(token, str):
            return False
        return any(hmac.compare_digest(token, t) for t in accepted)

    def _workspace_id(request):
        """Return the trusted workspace for tenant-scoped requests, if any."""
        context = getattr(request.state, "request_context", None) if request is not None else None
        workspace_id = getattr(context, "workspace_id", None) if context is not None else None
        return str(workspace_id) if workspace_id else None

    def _proposal_in_workspace(proposal, request) -> bool:
        workspace_id = _workspace_id(request)
        if workspace_id is None or getattr(
            getattr(request.state, "request_context", None), "species_privileges", False
        ):
            return True
        return bool(proposal is not None and getattr(proposal, "workspace_id", None) == workspace_id)

    def _task_in_workspace(task, request) -> bool:
        """Enforce explicit task ownership, retaining legacy proposal scoping.

        Workspace-owned tasks are never made visible through a proposal from a
        different workspace. Legacy tasks without workspace_id retain the
        existing proposal-based visibility rule.
        """
        workspace_id = _workspace_id(request)
        context = getattr(request.state, "request_context", None) if request is not None else None
        if workspace_id is None or getattr(context, "species_privileges", False):
            return True
        if task is None:
            return False
        task_workspace_id = getattr(task, "workspace_id", None)
        if task_workspace_id:
            return str(task_workspace_id) == workspace_id
        proposal_id = getattr(task, "proposal_id", None)
        proposal = director.proposals.get(proposal_id) if proposal_id else None
        return _proposal_in_workspace(proposal, request)

    def _trusted_actor(request, fallback: str = "admin") -> str:
        context = getattr(request.state, "request_context", None) if request is not None else None
        principal_id = getattr(context, "principal_id", None) if context is not None else None
        if principal_id:
            return str(principal_id)[:100]
        return fallback

    def _require_platform_scope(request) -> None:
        """Block tenant principals from mutating Director's shared singleton state."""
        context = getattr(request.state, "request_context", None) if request is not None else None
        if context is not None and getattr(context, "workspace_id", None):
            if not getattr(context, "species_privileges", False):
                from fastapi import HTTPException
                raise HTTPException(
                    status_code=403,
                    detail="This Director operation changes platform-wide state and requires platform operator privileges",
                )

    async def _require_auth(request, credentials=None, *, write: bool = False) -> None:
        if not _authenticate(credentials, request):
            if request is not None and getattr(request.state, "request_context", None) is None:
                master = (os.getenv("MASTER_API_TOKEN", "") or "").strip()
                service = (os.getenv("PYTHON_SERVICE_TOKEN", "") or "").strip()
                if not master and not service:
                    raise HTTPException(
                        status_code=503,
                        detail="Director authentication service is not configured",
                    )
            raise HTTPException(status_code=401, detail="Authentication required")

        if write and request is not None:
            context = getattr(request.state, "request_context", None)
            if context is not None and not getattr(context, "species_privileges", False):
                role = str(getattr(context, "role", "viewer") or "viewer").lower()
                if role not in {"operator", "admin", "owner"}:
                    raise HTTPException(
                        status_code=403,
                        detail="Director mutation requires operator role or above",
                    )

    class ApprovalBody(BaseModel):
        actor: str = Field(
            default="admin", min_length=1, max_length=100,
            pattern=r"^[a-zA-Z0-9_\-\.\s]+$",
        )

    class RejectBody(BaseModel):
        actor: str = Field(
            default="admin", min_length=1, max_length=100,
            pattern=r"^[a-zA-Z0-9_\-\.\s]+$",
        )
        reason: str = Field(default="", max_length=500)

    # ── Core ─────────────────────────────────────────────────────────

    @app.get("/health")
    async def health_check():
        return {
            "status": "healthy" if director.running else "stopped",
            "version": director.VERSION,
        }

    @app.get("/status")
    async def get_status(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        return director.status()

    @app.get("/organism/state")
    async def get_organism_state(
        request: Request,
        max_age_seconds: int = 300,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        """Expose current observations with explicit provenance and freshness."""
        await _require_auth(request, credentials)
        if max_age_seconds < 1 or max_age_seconds > 86400:
            raise HTTPException(400, "max_age_seconds must be between 1 and 86400")
        return director.organism_state(max_age_seconds=max_age_seconds)

    @app.get("/decision-context")
    async def get_decision_context(
        request: Request,
        max_age_seconds: int = 300,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        """Return the evidence context used to explain Director planning."""
        await _require_auth(request, credentials)
        if max_age_seconds < 1 or max_age_seconds > 86400:
            raise HTTPException(400, "max_age_seconds must be between 1 and 86400")
        return director.decision_context(max_age_seconds=max_age_seconds)

    @app.post("/strategic-plans")
    async def create_strategic_plan(
        body: StrategicObjectiveBody,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        """Create a validated advisory plan from a platform objective."""
        await _require_auth(request, credentials, write=True)
        _require_platform_scope(request)
        try:
            plan = await director.create_strategic_plan(
                body.dict(exclude_none=True)
            )
        except StrategicPlannerUnavailable as exc:
            # Provider failures occur before persistence. Return an explicit
            # no-side-effect retry contract so KCPG can safely reconcile the
            # transaction instead of treating the 503 as an unknown outcome.
            raise HTTPException(503, detail={
                "code": "STRATEGIC_PLANNER_UNAVAILABLE",
                "message": str(exc)[:200],
                "retryable": True,
                "side_effects": False,
            })
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except RuntimeError as exc:
            raise HTTPException(503, str(exc))
        if plan.get("status") == "unavailable":
            raise HTTPException(
                503,
                detail="Strategic planner is not configured; no plan was generated.",
            )
        return plan

    @app.get("/strategic-plans")
    async def list_strategic_plans(
        request: Request,
        limit: int = 50,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        await _require_auth(request, credentials)
        _require_platform_scope(request)
        if limit < 1 or limit > 100:
            raise HTTPException(400, "limit must be between 1 and 100")
        return {"plans": await director.list_strategic_plans(limit=limit)}

    @app.get("/strategic-plans/{plan_id}")
    async def get_strategic_plan(
        plan_id: str,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        await _require_auth(request, credentials)
        _require_platform_scope(request)
        plan = await director.load_strategic_plan(plan_id)
        if plan is None:
            raise HTTPException(404, "strategic plan not found")
        return plan

    @app.get("/strategic-plans/{plan_id}/verification")
    async def verify_strategic_plan(
        plan_id: str,
        request: Request,
        max_age_seconds: int = 300,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        """Return an independent, read-only verification report for a stored plan."""
        await _require_auth(request, credentials)
        _require_platform_scope(request)
        if max_age_seconds < 1 or max_age_seconds > 86400:
            raise HTTPException(400, "max_age_seconds must be between 1 and 86400")
        plan = await director.load_strategic_plan(plan_id)
        if plan is None:
            raise HTTPException(404, "strategic plan not found")
        return await director.verify_strategic_plan(
            plan, max_age_seconds=max_age_seconds
        )

    @app.post("/strategic-plans/{plan_id}/stage")
    async def stage_strategic_mission(
        plan_id: str,
        body: StrategicMissionAdmissionBody,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        """Stage an operator-parameterized plan as workspace-owned approval tasks."""
        await _require_auth(request, credentials, write=True)
        workspace_id = _workspace_id(request)
        if not workspace_id:
            raise HTTPException(400, "A trusted workspace context is required")
        try:
            return await director.stage_strategic_mission(
                plan_id=plan_id,
                workspace_id=workspace_id,
                idempotency_key=body.idempotency_key,
                step_parameters=body.step_parameters,
                actor=_trusted_actor(request),
            )
        except KeyError:
            raise HTTPException(404, "Strategic plan not found")
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        except RuntimeError as exc:
            log.error("Director mission admission unavailable: %s", str(exc)[:200])
            raise HTTPException(503, "Durable mission admission is unavailable")
    @app.get("/missions/graph")
    async def get_mission_graph(
        request: Request,
        mission_id: Optional[str] = None,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        """Expose the current Director task graph, scoped to the caller."""
        await _require_auth(request, credentials)
        graph = director.mission_graph(mission_id=mission_id)
        visible_ids = {
            node["task_id"] for node in graph["nodes"]
            if _task_in_workspace(director.tasks.get(node["task_id"]), request)
        }
        graph["nodes"] = [node for node in graph["nodes"] if node["task_id"] in visible_ids]
        graph["edges"] = [
            edge for edge in graph["edges"]
            if edge["dependent"] in visible_ids and edge["prerequisite"] in visible_ids
        ]
        return graph

    @app.post("/tasks/{task_id}/dependencies/{dependency_id}")
    async def add_mission_dependency(
        task_id: str,
        dependency_id: str,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        """Add a prerequisite edge; both tasks must be visible in this workspace."""
        await _require_auth(request, credentials)
        task = director.tasks.get(task_id)
        dependency = director.tasks.get(dependency_id)
        if task is None or dependency is None:
            raise HTTPException(404, "task or dependency not found")
        if not _task_in_workspace(task, request) or not _task_in_workspace(dependency, request):
            raise HTTPException(404, "task or dependency not found")
        try:
            return await director.add_task_dependency(task_id, dependency_id)
        except KeyError:
            raise HTTPException(404, "task or dependency not found")
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.get("/briefing")
    async def get_briefing(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        return director.briefings[-1] if director.briefings else director.briefing()

    @app.get("/tasks/{task_id}/recovery-assessment")
    async def get_task_recovery_assessment(
        task_id: str,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        """Return scoped recovery guidance; this endpoint never retries tasks."""
        await _require_auth(request, credentials)
        task = director.tasks.get(task_id)
        if task is None or not _task_in_workspace(task, request):
            raise HTTPException(404, "task not found")
        assessment = director.assess_task_recovery(task_id)
        visible_dependencies = []
        for dependency in assessment["dependencies"]:
            dependency_task = director.tasks.get(dependency["task_id"])
            if dependency_task is not None and not _task_in_workspace(dependency_task, request):
                raise HTTPException(404, "task not found")
            visible_dependencies.append(dependency)
        assessment["dependencies"] = visible_dependencies
        return assessment

    @app.post("/tasks/{task_id}/verify-outcome")
    async def verify_task_outcome(
        task_id: str,
        request: Request,
        max_age_seconds: int = 300,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        """Reconcile completed tasks against fresh structured observer evidence."""
        await _require_auth(request, credentials, write=True)
        if max_age_seconds < 1 or max_age_seconds > 86400:
            raise HTTPException(400, "max_age_seconds must be between 1 and 86400")
        task = director.tasks.get(task_id)
        if task is None or not _task_in_workspace(task, request):
            raise HTTPException(404, "task not found")
        try:
            return await director.evaluate_task_success_criteria(task_id, max_age_seconds=max_age_seconds)
        except KeyError:
            raise HTTPException(404, "task not found")
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.get("/tasks")
    async def get_tasks(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        return [safe(asdict(t)) for t in director.tasks.values() if _task_in_workspace(t, request)]

    @app.get("/approvals")
    async def get_approvals(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        return [
            safe(asdict(t)) for t in director.tasks.values()
            if t.status == Status.APPROVAL and _task_in_workspace(t, request)
        ]

    @app.post("/tasks/{task_id}/approve")
    async def approve_task(
        task_id: str,
        body: ApprovalBody,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        await _require_auth(request, credentials, write=True)
        task = director.tasks.get(task_id)
        if task is not None and not _task_in_workspace(task, request):
            raise HTTPException(404, "Task not found")
        try:
            return safe(asdict(director.approve(task_id, _trusted_actor(request, body.actor))))
        except KeyError:
            raise HTTPException(404, "Task not found")
        except ValueError as e:
            raise HTTPException(409, str(e))

    @app.post("/tasks/{task_id}/reject")
    async def reject_task(
        task_id: str,
        body: RejectBody,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        await _require_auth(request, credentials, write=True)
        task = director.tasks.get(task_id)
        if task is not None and not _task_in_workspace(task, request):
            raise HTTPException(404, "Task not found")
        try:
            return safe(asdict(director.reject(task_id, _trusted_actor(request, body.actor), body.reason)))
        except KeyError:
            raise HTTPException(404, "Task not found")

    @app.get("/engines")
    async def get_engines(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        return {
            "engines": {
                name: {
                    "name": name,
                    "status": obs.status.value,
                    "sector": obs.sector.value,
                    "issues": len(obs.issues),
                }
                for name, obs in director.observations.items()
            },
            "total": len(director.observations),
        }

    # ── Diagnostics ──────────────────────────────────────────────────

    @app.get("/diagnostics")
    async def get_diagnostics(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        return {
            "root_cause": director.current_root_cause.__dict__ if director.current_root_cause else None,
            "impact": director.current_impact,
            "cascading_predictions": director.current_predictions[:5],
            "recovery_plans": director.recovery_coordinator.get_recovery_plans(),
            "evidence_gate": director.last_diagnosis_evidence,
        }

    @app.get("/diagnostics/root-cause")
    async def get_root_cause(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        result = director.current_root_cause
        return {
            "status": "found" if result else "none",
            "data": safe(asdict(result)) if result else None,
        }

    @app.get("/diagnostics/cascading")
    async def get_cascading(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        return {"predictions": director.current_predictions}

    @app.get("/diagnostics/impact")
    async def get_impact(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        return director.current_impact

    @app.get("/diagnostics/recovery-plans")
    async def get_recovery_plans(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        return {"recovery_plans": director.recovery_coordinator.get_recovery_plans()}

    # ── Decisions & Alerts ───────────────────────────────────────────

    @app.get("/decisions")
    async def get_decisions(
        request: Request,
        limit: int = 50,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        await _require_auth(request, credentials)
        return [safe(asdict(d)) for d in list(director.decisions)[-limit:]]

    @app.get("/alerts")
    async def get_alerts(
        request: Request,
        limit: int = 50,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        await _require_auth(request, credentials)
        return {"alerts": list(director._alert_history)[-limit:]}

    # ── Capabilities ─────────────────────────────────────────────────

    @app.get("/capabilities")
    async def get_capabilities(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        catalog = director.capability_catalog()
        issue_counts = {}
        for item in catalog.values():
            for issue in item["issues"]:
                issue_counts[issue] = issue_counts.get(issue, 0) + 1

        by_sector = {}
        for item in catalog.values():
            sector = item["sector"]
            row = by_sector.setdefault(sector, {"total": 0, "ready": 0, "not_ready": 0, "runtime_bound": 0})
            row["total"] += 1
            row["ready"] += int(bool(item["ready"]))
            row["not_ready"] += int(not item["ready"])
            row["runtime_bound"] += int(bool(item["runtime_binding_confirmed"]))

        summary = {
            "total": len(catalog),
            "binding_report_available": bool(getattr(director, "capability_binding_report", {})),
            "binding_reported_at": getattr(director, "capability_binding_at", None),
            "runtime_bound": sum(1 for item in catalog.values() if item["runtime_binding_confirmed"]),
            "runtime_binding_unconfirmed": sum(1 for item in catalog.values() if not item["runtime_binding_confirmed"]),
            "runtime_dependencies_verified": sum(1 for item in catalog.values() if item["runtime_dependencies_verified"]),
            "runtime_dependencies_unverified": sum(1 for item in catalog.values() if not item["runtime_dependencies_verified"]),
            "ready": sum(1 for item in catalog.values() if item["ready"]),
            "not_ready": sum(1 for item in catalog.values() if not item["ready"]),
            "proposal_capable": sum(1 for item in catalog.values() if item["proposal_capable"]),
            "missing_governed_handoff": issue_counts.get("governed_handoff_missing", 0),
            "approval_required": sum(1 for item in catalog.values() if item["approval_required"]),
            "missing_outcome_verification": issue_counts.get("outcome_verification_missing", 0),
            "missing_production_handler": issue_counts.get("production_handler_missing", 0),
            "disabled": issue_counts.get("disabled", 0),
            "safety_blocked": issue_counts.get("safety_blocked_handler", 0),
            "issue_counts": dict(sorted(issue_counts.items())),
            "by_sector": dict(sorted(by_sector.items())),
            "execution_paths": {
                path: sum(1 for item in catalog.values() if item["execution_path"] == path)
                for path in sorted({item["execution_path"] for item in catalog.values()})
            },
            # Binding evidence is not an integration test. Keep this explicit
            # until deployment-specific dependency probes provide evidence.
            "end_to_end_runtime_verified": False,
            "readiness_scope": "static_wiring_and_contracts_only",
        }
        return {"capabilities": catalog, "summary": summary}

    @app.post("/capabilities/{cap_name}/toggle")
    async def toggle_capability(
        cap_name: str,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        await _require_auth(request, credentials, write=True)
        _require_platform_scope(request)
        cap = director.capabilities.get(cap_name)
        if not cap:
            raise HTTPException(404, f"Unknown capability: {cap_name}")
        cap.enabled = not cap.enabled
        return {"capability": cap_name, "enabled": cap.enabled}

    @app.post("/capabilities/{cap_name}/execute")
    async def execute_capability_endpoint(
        cap_name: str,
        body: dict,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        """Manually invoke a capability. Useful for testing."""
        await _require_auth(request, credentials, write=True)
        _require_platform_scope(request)
        result = await director.execute_capability(cap_name, body.get("params", {}))
        return result

    # ── Proposals and Signals (v5.5) ─────────────────────────────────

    @app.get("/proposals")
    async def list_proposals(
        request: Request,
        status: Optional[str] = None,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        await _require_auth(request, credentials)
        result = [
            safe(asdict(p))
            for p in director.proposals.values()
            if _proposal_in_workspace(p, request)
            and (status is None or p.status.value == status)
        ]
        return {"proposals": result, "count": len(result)}

    @app.get("/proposals/{proposal_id}")
    async def get_proposal(
        proposal_id: str,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        await _require_auth(request, credentials)
        proposal = director.proposals.get(proposal_id)
        if proposal is None or not _proposal_in_workspace(proposal, request):
            raise HTTPException(404, f"Proposal {proposal_id} not found")
        return safe(asdict(proposal))

    @app.post("/proposals/{proposal_id}/approve")
    async def approve_proposal(
        proposal_id: str,
        body: dict,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        await _require_auth(request, credentials, write=True)
        try:
            proposal = director.proposals.get(proposal_id)
            if proposal is None or not _proposal_in_workspace(proposal, request):
                raise HTTPException(404, f"Proposal {proposal_id} not found")
            task = director.approve_proposal(
                proposal_id,
                actor=_trusted_actor(request, body.get("actor", "admin")),
            )

            # Approval is not execution authority. Execution is left to the
            # Director scheduler, which is the single place that invokes the
            # capability's governed handoff. Calling the handoff here as well
            # would create a second path and could execute the same proposal
            # twice (API request + scheduler pass).
            return {
                "ok": True,
                "execution": {
                    "status": "deferred",
                    "reason": "awaiting_governed_scheduler_handoff",
                    "note": "Approval was recorded; execution has not been claimed.",
                },
                "proposal": safe(asdict(director.proposals[proposal_id])),
                "task": safe(asdict(task)),
            }
        except KeyError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(409, str(e))

    @app.post("/proposals/{proposal_id}/reject")
    async def reject_proposal(
        proposal_id: str,
        body: dict,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        await _require_auth(request, credentials, write=True)
        try:
            existing = director.proposals.get(proposal_id)
            if existing is None or not _proposal_in_workspace(existing, request):
                raise HTTPException(404, f"Proposal {proposal_id} not found")
            proposal = director.reject_proposal(
                proposal_id,
                actor=_trusted_actor(request, body.get("actor", "admin")),
                reason=body.get("reason", ""),
            )
            return {"ok": True, "proposal": safe(asdict(proposal))}
        except KeyError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(409, str(e))

    @app.get("/signals")
    async def list_signals(
        request: Request,
        limit: int = 50,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        await _require_auth(request, credentials)
        recent = sorted(
            (signal for signal in director.signals.values()
             if _workspace_id(request) is None
             or getattr(getattr(request.state, "request_context", None), "species_privileges", False)
             or getattr(signal, "workspace_id", None) == _workspace_id(request)),
            key=lambda s: s.detected_at,
            reverse=True,
        )[:limit]
        return {
            "signals": [safe(asdict(s)) for s in recent],
            "count": len(recent),
            "total": sum(
                1 for signal in director.signals.values()
                if _workspace_id(request) is None
                or getattr(getattr(request.state, "request_context", None), "species_privileges", False)
                or getattr(signal, "workspace_id", None) == _workspace_id(request)
            ),
        }

    @app.post("/detectors/run")
    async def run_detectors(
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        """Force a detector run (useful for testing)."""
        await _require_auth(request, credentials, write=True)
        _require_platform_scope(request)
        await director._run_detectors()
        return {
            "ok": True,
            "signals": len(director.signals),
            "proposals": len(director.proposals),
        }

    # ── Control ──────────────────────────────────────────────────────

    @app.post("/observe")
    async def run_observe(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials, write=True)
        _require_platform_scope(request)
        await director._observe_async()
        return director.status()

    @app.post("/plan")
    async def run_plan(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials, write=True)
        _require_platform_scope(request)
        return await director._build_plan_async()

    @app.post("/start")
    async def run_start(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials, write=True)
        _require_platform_scope(request)
        await director.start()
        return director.status()

    @app.post("/stop")
    async def run_stop(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials, write=True)
        _require_platform_scope(request)
        await director.stop()
        return director.status()

    # ── Intelligence ─────────────────────────────────────────────────

    @app.get("/outlook")
    async def get_outlook(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        outlook = director.generate_strategic_outlook()
        return safe(asdict(outlook)) if outlook else {"error": "Insufficient data"}

    @app.get("/allocation")
    async def get_allocation(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        return [safe(asdict(a)) for a in director.allocate_attention()]

    @app.get("/summary")
    async def get_summary(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        return {"summary": director.generate_executive_summary()}

    @app.get("/initiatives")
    async def get_initiatives(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        return {"initiatives": director.generate_initiatives()}

    @app.get("/correlations")
    async def get_correlations(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        return {"correlations": director.correlate_observations()}

    @app.get("/daily-briefing")
    async def get_daily_briefing(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        director._send_daily_briefing()
        return {"ok": True, "message": "Daily briefing sent"}

    # ── Learning Audit ───────────────────────────────────────────────

    @app.get("/learning/report")
    async def get_learning_report(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        return director.learning_loop.get_learning_report()

    @app.post("/learning/rebuild")
    async def rebuild_learning(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials, write=True)
        before = len(director.learning_loop.signals)
        await director._replay_learning_history()
        after = len(director.learning_loop.signals)
        return {
            "ok": True,
            "signals_loaded": after - before,
            "signals_total": after,
            "capabilities_tracked": len(director.learning_loop.capability_stats),
            "targets_tracked": len(director.learning_loop.target_failure_counts),
        }

    @app.get("/learning/capability/{capability}")
    async def get_capability_learning(
        capability: str,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        await _require_auth(request, credentials)
        all_time = director.learning_loop.get_success_rate(capability)
        recent = director.learning_loop.get_recent_success_rate(capability)
        avg_duration = director.learning_loop.get_avg_duration_ms(capability)

        context_breakdown = {}
        for ctx_key, stats in director.learning_loop.context_stats.items():
            if not ctx_key.endswith(f":{capability}") and f":{capability}:" not in ctx_key:
                continue
            parts = ctx_key.split(":")
            if len(parts) < 2:
                continue
            sector_name = parts[0]
            target = parts[2] if len(parts) >= 3 else None
            key = f"{sector_name}:{target}" if target else sector_name
            total = stats["successes"] + stats["failures"]
            context_breakdown[key] = {
                "successes": stats["successes"],
                "failures": stats["failures"],
                "success_rate": round(stats["successes"] / total, 3) if total > 0 else None,
                "total": total,
            }

        time_breakdown = {}
        for time_key, stats in director.learning_loop.time_stats.items():
            if f":{capability}:" not in time_key:
                continue
            parts = time_key.split(":")
            if len(parts) < 3:
                continue
            time_bucket = parts[-1]
            time_breakdown.setdefault(time_bucket, {"successes": 0, "failures": 0})
            time_breakdown[time_bucket]["successes"] += stats["successes"]
            time_breakdown[time_bucket]["failures"] += stats["failures"]

        for bucket, stats in time_breakdown.items():
            total = stats["successes"] + stats["failures"]
            stats["success_rate"] = round(stats["successes"] / total, 3) if total > 0 else None
            stats["total"] = total

        recent_failures = list(director.learning_loop.failure_patterns.get(capability, []))[-10:]

        time_context = director.learning_loop._get_time_context()
        should_escalate = False
        should_skip = False
        for sector in Sector:
            if director.learning_loop.should_escalate(sector, capability):
                should_escalate = True
            if director.learning_loop.should_skip(sector, capability):
                should_skip = True

        return {
            "capability": capability,
            "all_time_success_rate": round(all_time, 3) if all_time is not None else None,
            "recent_success_rate": round(recent, 3) if recent is not None else None,
            "avg_duration_ms": round(avg_duration, 1) if avg_duration is not None else None,
            "context_breakdown": context_breakdown,
            "time_of_day_breakdown": time_breakdown,
            "current_time_context": time_context,
            "recent_failures": [
                {
                    "timestamp": f.get("timestamp").isoformat() if f.get("timestamp") else None,
                    "error": f.get("error"),
                    "error_class": f.get("error_class"),
                    "context": f.get("context"),
                    "target": f.get("target"),
                }
                for f in recent_failures
            ],
            "current_trust": {
                "should_escalate": should_escalate,
                "should_skip": should_skip,
                "verdict": (
                    "skip" if should_skip
                    else "escalate" if should_escalate
                    else "trust"
                ),
            },
        }

    @app.get("/learning/contexts")
    async def get_all_contexts(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        contexts = {}
        for ctx_key, stats in director.learning_loop.context_stats.items():
            total = stats["successes"] + stats["failures"]
            contexts[ctx_key] = {
                "successes": stats["successes"],
                "failures": stats["failures"],
                "success_rate": round(stats["successes"] / total, 3) if total > 0 else None,
                "total": total,
                "verdict": (
                    "skip" if total >= 3 and (stats["successes"] / total) < 0.2
                    else "escalate" if total >= 3 and (stats["successes"] / total) < 0.5
                    else "trust" if total >= 3
                    else "insufficient_data"
                ),
            }
        return {"total_contexts": len(contexts), "contexts": contexts}

    @app.get("/learning/time-patterns")
    async def get_time_patterns(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        by_capability: Dict[str, Dict[str, Dict[str, int]]] = {}
        for time_key, stats in director.learning_loop.time_stats.items():
            parts = time_key.split(":")
            if len(parts) < 3:
                continue
            capability_name = parts[1]
            time_bucket = parts[-1]
            by_capability.setdefault(capability_name, {})
            by_capability[capability_name].setdefault(time_bucket, {"successes": 0, "failures": 0})
            by_capability[capability_name][time_bucket]["successes"] += stats["successes"]
            by_capability[capability_name][time_bucket]["failures"] += stats["failures"]

        result = {}
        for capability, buckets in by_capability.items():
            result[capability] = {}
            for bucket, stats in buckets.items():
                total = stats["successes"] + stats["failures"]
                result[capability][bucket] = {
                    "successes": stats["successes"],
                    "failures": stats["failures"],
                    "success_rate": round(stats["successes"] / total, 3) if total > 0 else None,
                    "total": total,
                }

        return {
            "current_time_context": director.learning_loop._get_time_context(),
            "current_day_context": director.learning_loop._get_day_context(),
            "patterns": result,
        }

    @app.get("/learning/failures")
    async def get_recent_failures(
        request: Request,
        limit: int = 50,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        await _require_auth(request, credentials)
        recent = [s for s in director.learning_loop.signals if s.outcome == "failure"]
        recent = list(reversed(recent))[:limit]
        return {
            "total_recent_failures": len(recent),
            "failures": [
                {
                    "capability": s.capability,
                    "sector": s.sector.value,
                    "timestamp": s.timestamp.isoformat(),
                    "error": s.error_type,
                    "duration_ms": round(s.duration_ms, 1),
                    "context": s.context,
                    "target": s.context.get("target") if isinstance(s.context, dict) else None,
                    "error_class": s.context.get("error_class") if isinstance(s.context, dict) else None,
                }
                for s in recent
            ],
        }

    @app.get("/learning/verification")
    async def get_verification_report(
        request: Request,
        limit: int = 50,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        """Show recent VERIFIED vs INEFFECTIVE outcomes."""
        await _require_auth(request, credentials)
        verified = [
            safe(asdict(d)) for d in list(director.decisions)
            if d.outcome == "verified"
        ][-limit:]
        ineffective = [
            safe(asdict(d)) for d in list(director.decisions)
            if d.outcome == "ineffective"
        ][-limit:]
        total_verified = len([d for d in director.decisions if d.outcome == "verified"])
        total_ineffective = len([d for d in director.decisions if d.outcome == "ineffective"])
        return {
            "verified_count": total_verified,
            "ineffective_count": total_ineffective,
            "verified_recent": verified,
            "ineffective_recent": ineffective,
            "effective_rate": (
                total_verified / (total_verified + total_ineffective)
                if (total_verified + total_ineffective) > 0 else None
            ),
        }

    @app.get("/learning/targets")
    async def get_learning_targets(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        targets = []
        for tkey, count in director.learning_loop.target_failure_counts.items():
            if ":" not in tkey:
                continue
            capability, target = tkey.split(":", 1)
            last_error = director.learning_loop.target_last_error.get(tkey, "UNKNOWN")
            targets.append({
                "capability": capability,
                "target": target,
                "failure_count": count,
                "last_error_class": last_error,
                "is_permanent_error": last_error in LearningLoop.PERMANENT_ERROR_CLASSES,
                "would_skip": count >= 3 or last_error in LearningLoop.PERMANENT_ERROR_CLASSES,
            })
        targets.sort(key=lambda x: (x["capability"], x["target"]))
        return {"total_targets": len(targets), "targets": targets}

    @app.get("/learning/slowest")
    async def get_slowest_capabilities(
        request: Request,
        limit: int = 10,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        await _require_auth(request, credentials)
        durations = []
        for capability in director.learning_loop.duration_stats.keys():
            avg_ms = director.learning_loop.get_avg_duration_ms(capability)
            if avg_ms is not None:
                durations.append({
                    "capability": capability,
                    "avg_duration_ms": round(avg_ms, 1),
                    "sample_size": len(director.learning_loop.duration_stats[capability]),
                })
        durations.sort(key=lambda x: x["avg_duration_ms"], reverse=True)
        return {"total_tracked": len(durations), "slowest": durations[:limit]}

    @app.post("/learning/reset/{capability}")
    async def reset_capability_learning(
        capability: str,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        await _require_auth(request, credentials, write=True)
        _require_platform_scope(request)
        director.learning_loop.capability_stats.pop(capability, None)
        director.learning_loop.duration_stats.pop(capability, None)
        director.learning_loop.failure_patterns.pop(capability, None)
        keys_to_remove = [
            k for k in director.learning_loop.context_stats.keys()
            if k.endswith(f":{capability}") or f":{capability}:" in k
        ]
        for k in keys_to_remove:
            del director.learning_loop.context_stats[k]
        time_keys_to_remove = [
            k for k in director.learning_loop.time_stats.keys()
            if f":{capability}:" in k
        ]
        for k in time_keys_to_remove:
            del director.learning_loop.time_stats[k]
        target_keys_to_remove = [
            k for k in director.learning_loop.target_failure_counts.keys()
            if k.startswith(f"{capability}:")
        ]
        for k in target_keys_to_remove:
            del director.learning_loop.target_failure_counts[k]
            director.learning_loop.target_last_error.pop(k, None)
        director.learning_loop.signals = type(director.learning_loop.signals)(
            [s for s in director.learning_loop.signals if s.capability != capability],
            maxlen=director.learning_loop.signals.maxlen or 1000,
        )
        log.info("Learning reset for capability: %s", capability)
        return {
            "ok": True,
            "capability": capability,
            "message": f"Learning data cleared for {capability}",
        }

    # ── OODA Introspection ───────────────────────────────────────────

    async def _get_autonomous_loop():
        try:
            from core.autonomous_loop import get_autonomous_loop
            return await get_autonomous_loop()
        except Exception:
            return None

    @app.get("/ooda/state")
    async def get_ooda_state(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        loop = await _get_autonomous_loop()
        if loop is None:
            return {"ok": False, "status": "unavailable", "message": "Autonomous Loop is not running in this process."}
        try:
            return {"ok": True, "state": loop.get_ooda_state()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/ooda/world-model")
    async def get_world_model(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        loop = await _get_autonomous_loop()
        if loop is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "world_model": loop.get_world_model()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/ooda/priorities")
    async def get_ooda_priorities(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        loop = await _get_autonomous_loop()
        if loop is None:
            return {"ok": False, "status": "unavailable"}
        try:
            priorities = dict(sorted(
                loop._observation_priorities.items(),
                key=lambda x: x[1],
                reverse=True,
            ))
            return {"ok": True, "priorities": priorities}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/ooda/action-preferences")
    async def get_ooda_action_preferences(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        loop = await _get_autonomous_loop()
        if loop is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "preferences": loop.get_action_preferences()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/ooda/causal-insights")
    async def get_ooda_causal_insights(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        loop = await _get_autonomous_loop()
        if loop is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "insights": loop.get_causal_insights()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/ooda/learner-report")
    async def get_ooda_learner_report(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        loop = await _get_autonomous_loop()
        if loop is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "report": loop.get_learner_report()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/ooda/intelligence-report")
    async def get_ooda_intelligence_report(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        loop = await _get_autonomous_loop()
        if loop is None:
            return {"ok": False, "status": "unavailable"}
        try:
            report = await loop.get_intelligence_report()
            return {"ok": True, "report": report}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    # ── Meta Engine Introspection ────────────────────────────────────

    async def _get_meta_engine():
        try:
            from services.meta_engine import meta_engine
            return meta_engine
        except Exception:
            return None

    @app.get("/meta/status")
    async def get_meta_status(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        meta = await _get_meta_engine()
        if meta is None:
            return {"ok": False, "status": "unavailable", "message": "Meta Engine not initialized in this process."}
        try:
            return {"ok": True, "status": meta.get_status()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/meta/impact")
    async def get_meta_impact(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        meta = await _get_meta_engine()
        if meta is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "impact": meta.get_impact_assessment()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/meta/topology")
    async def get_meta_topology(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        meta = await _get_meta_engine()
        if meta is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "topology": meta.get_topology()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/meta/root-cause")
    async def get_meta_root_cause(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        meta = await _get_meta_engine()
        if meta is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "root_cause": meta.get_root_cause()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/meta/cascading")
    async def get_meta_cascading(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        meta = await _get_meta_engine()
        if meta is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "predictions": meta.get_cascading_predictions()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/meta/recovery-plans")
    async def get_meta_recovery_plans(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        meta = await _get_meta_engine()
        if meta is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "plans": meta.get_recovery_plans()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/meta/alerts")
    async def get_meta_alerts(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        meta = await _get_meta_engine()
        if meta is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "alerts": meta.get_alerts()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/meta/incidents")
    async def get_meta_incidents(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        meta = await _get_meta_engine()
        if meta is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "incidents": meta.get_incidents()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/meta/learning/report")
    async def get_meta_learning_report(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None):
        await _require_auth(request, credentials)
        meta = await _get_meta_engine()
        if meta is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "report": meta.get_learning_report()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/meta/learning/engine/{engine_name}")
    async def get_meta_engine_pattern(
        engine_name: str,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        await _require_auth(request, credentials)
        meta = await _get_meta_engine()
        if meta is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "pattern": meta.get_engine_pattern(engine_name)}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/meta/engine/{engine_name}")
    async def get_meta_engine_report(
        engine_name: str,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        await _require_auth(request, credentials)
        meta = await _get_meta_engine()
        if meta is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "report": meta.get_engine_report(engine_name)}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}


    @app.get("/platform-state")
    async def get_platform_state(
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ):
        """Read the authoritative platform snapshot; tenant scopes are denied."""
        await _require_auth(request, credentials)
        _require_platform_scope(request)
        context = getattr(request.state, "request_context", None)
        # A tenant role (including workspace owner/admin) is never sufficient
        # for platform-wide state. Only the trusted platform/species privilege
        # or a separately authenticated platform token may read this snapshot.
        if context is not None and not getattr(context, "species_privileges", False):
            raise HTTPException(
                status_code=403,
                detail="Platform State requires platform operator privileges",
            )
        try:
            from services.supabase import get_service_client_from_settings
            from core.platform_state_reader import PlatformStateReader

            client = await asyncio.to_thread(get_service_client_from_settings)
            snapshot = await PlatformStateReader(client).get_snapshot()
        except Exception:
            log.warning("Platform State read unavailable", exc_info=True)
            raise HTTPException(
                status_code=503,
                detail="Platform State is temporarily unavailable",
            )
        if snapshot is None:
            return {
                "status": "uninitialized",
                "scope_type": "platform",
                "scope_id": "platform",
                "revision": 0,
                "services": [],
                "capabilities": [],
            }
        return {
            "status": "available",
            "scope_type": snapshot.scope_type,
            "scope_id": snapshot.scope_id,
            "revision": snapshot.revision,
            "schema_version": snapshot.schema_version,
            "updated_at": snapshot.updated_at.isoformat(),
            "services": [
                {
                    "service_id": item.service_id,
                    "owner": item.owner,
                    "readiness": item.readiness.value,
                    "observed_at": item.observed_at.isoformat() if item.observed_at else None,
                    "source_version": item.source_version,
                    "dependencies": list(item.dependencies),
                    "capabilities": list(item.capabilities),
                    "critical": item.critical,
                    "reason": item.reason,
                    "evidence": [
                        {
                            "source": evidence.source,
                            "observed_at": evidence.observed_at.isoformat(),
                            "source_version": evidence.source_version,
                            "correlation_id": evidence.correlation_id,
                            "reference": evidence.reference,
                        }
                        for evidence in item.evidence
                    ],
                }
                for item in snapshot.services
            ],
            "capabilities": [
                {
                    "capability_id": item.capability_id,
                    "owner_service": item.owner_service,
                    "readiness": item.readiness.value,
                    "observed_at": item.observed_at.isoformat() if item.observed_at else None,
                    "reason": item.reason,
                }
                for item in snapshot.capabilities
            ],
        }

    return app


# ═══════════════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

director: Optional[Director] = None
_lock = asyncio.Lock()


async def init_director(supabase_client=None, auto_start: bool = True, strategic_planner=None) -> Director:
    global director
    async with _lock:
        if director is None:
            director = Director(supabase_client=supabase_client, strategic_planner=strategic_planner)
        elif strategic_planner is not None:
            director.strategic_planner = strategic_planner
        if auto_start and not director.running:
            await director.start()
    return director


async def get_director() -> Optional[Director]:
    return director


async def shutdown_director() -> None:
    global director
    if director is not None:
        await director.stop()
        director = None


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Kanyoza Director")
    parser.add_argument("--api", action="store_true", help="Run as API server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()

    d = Director()

    if args.api:
        import uvicorn
        app = create_app(d)

        async def _startup():
            await d.start()

        @app.on_event("startup")
        async def _on_startup():
            await _startup()

        @app.on_event("shutdown")
        async def _on_shutdown():
            await d.stop()

        uvicorn.run(app, host=args.host, port=args.port)
    else:
        async def _run():
            await d.start()
            try:
                while d.running:
                    await asyncio.sleep(5)
            except KeyboardInterrupt:
                await d.stop()

        asyncio.run(_run())


if __name__ == "__main__":
    main()


__all__ = [
    "create_app",
    "director",
    "init_director",
    "get_director",
    "shutdown_director",
]
