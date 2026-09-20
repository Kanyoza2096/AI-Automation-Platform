"""
Canonical publish intelligence pipeline (Phase D gaps closed).

  Content → Guardian → Metabolism → Council → Director
    → Outbox (required when durable) → Worker/Connector → Outcome → Memory

Hard rules:
  - Guardian BLOCK → stop
  - Metabolism deny / unknown critical failure → stop (fail closed)
  - Council veto → stop
  - Outbox enqueue failure when durable=True → stop (no side effect)
  - Outcome recorded only after real/simulated execution completes
  - dry_run never enqueues or records success outcome

Receipt contract (v2):
  - A pipeline-level "ok" is only valid when the connector returned a
    concrete provider object ID. Anything less is classified as "uncertain"
    and propagated honestly.
  - The simulated fallback is dev-only. It never returns "ok".
  - page_id and account_id are surfaced on every result so downstream layers
    can persist them for audit and reconciliation. The page token is never
    included.

DIAG (temporary):
  - `DIAG _connector_execute` WARNING lines trace plugin resolution,
    the exact plugin type being called, and the exit status. Remove once
    the connector path is verified in production.
"""
from __future__ import annotations

import base64
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("kanyoza.domain.publishing.canonical_pipeline")


@dataclass
class PipelineStage:
    stage: str
    status: str
    started_at: str = ""
    completed_at: str = ""
    duration_ms: Optional[float] = None
    error_code: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "stage": self.stage,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "error_code": self.error_code,
        }
        d.update(self.detail)
        return d


@dataclass
class PipelineTrace:
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    stages: List[PipelineStage] = field(default_factory=list)

    def mark(
        self,
        stage: str,
        status: str,
        *,
        error_code: Optional[str] = None,
        started: Optional[datetime] = None,
        **detail: Any,
    ) -> PipelineStage:
        now = datetime.now(timezone.utc)
        started_at = (started or now).isoformat()
        completed_at = now.isoformat()
        duration_ms = None
        if started is not None:
            duration_ms = round((now - started).total_seconds() * 1000, 2)
        rec = PipelineStage(
            stage=stage,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            error_code=error_code,
            detail=detail,
        )
        self.stages.append(rec)
        return rec

    def as_list(self) -> List[Dict[str, Any]]:
        return [s.to_dict() for s in self.stages]


class PipelineBlocked(Exception):
    def __init__(self, code: str, message: str, trace: PipelineTrace):
        super().__init__(message)
        self.code = code
        self.trace = trace


async def run_canonical_publish(
    *,
    container=None,
    workspace_id: str,
    brand_id: Optional[str] = None,
    platform: Optional[str] = None,
    topic: str = "",
    caption: str = "",
    dry_run: bool = False,
    source: str = "api",
    risk_level: str = "low",
    decision_id: Optional[str] = None,
    scheduled_post_id: Optional[str] = None,
    durable: bool = True,
    _from_outbox: bool = False,
    require_council: bool = False,
    brand: Optional[Any] = None,
    automation_job_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    image_url: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
    to_number: Optional[str] = None,
    recipient: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    from core.publish_authorization import assert_publish_allowed
    from core.decision_gate import DecisionGateError, decision_gate
    from core.metabolism import MetabolismError

    trace = PipelineTrace()
    ws = (workspace_id or "").strip()
    if not ws:
        return {
            "ok": False,
            "code": "workspace_required",
            "error": "workspace_id is required (no empty-tenant default)",
            "trace": trace.as_list(),
            "correlation_id": trace.correlation_id,
        }

    platform = (platform or "facebook").strip().lower()

    try:
        from domain.publishing.provider_contract import validate_request
        contract_error = validate_request(
            platform,
            text=(caption or topic or "").strip(),
            image_url=image_url,
            image_bytes=image_bytes,
            recipient=to_number or recipient,
        )
        if contract_error:
            return _fail(contract_error["error_code"], contract_error["error"], trace)
    except Exception as exc:
        return _fail("provider_contract_unavailable", f"Publishing contract unavailable: {exc}", trace)

    if brand is None and brand_id:
        try:
            if container is not None and hasattr(container, "try_resolve"):
                from repositories.brand_repository import BrandRepository
                repo = container.try_resolve(BrandRepository)
                if repo is not None:
                    brand = repo.find_by_id(brand_id)
            if brand is None:
                from repositories.brand_repository import BrandRepository
                brand = BrandRepository().find_by_id(brand_id)
        except Exception as exc:
            logger.warning("Brand resolution unavailable for publish brand=%s: %s", brand_id, exc)

    if brand is not None and str(getattr(brand, "workspace_id", "")) != ws:
        return _fail("brand_workspace_mismatch", "brand does not belong to publish workspace", trace)

    if not idempotency_key:
        try:
            from core.workspace_context import get_current_social_account
            social_account_id = get_current_social_account() or ""
        except Exception:
            social_account_id = ""
        from core.execution_identity import make_publish_idempotency_key
        idempotency_key = make_publish_idempotency_key(
            ws, brand_id or "", platform, social_account_id,
            topic or "", caption or "", image_url or "", image_bytes
        )

    content = {
        "topic": topic or "",
        "caption": caption or "",
        "brand_id": brand_id,
        "platform": platform,
        "risk_level": risk_level,
        "source": source,
        "idempotency_key": idempotency_key,
        "dry_run": dry_run,
    }
    t0 = datetime.now(timezone.utc)
    trace.mark("content", "ok", started=t0, caption_len=len(content["caption"]))

    # ── Guardian ────────────────────────────────────────────────────────
    g = await _guardian_evaluate(ws, content, container)
    trace.mark("guardian", g["status"], error_code=g.get("error_code"), **{k: v for k, v in g.items() if k not in ("status", "error_code")})
    if g["status"] in ("block", "quarantine"):
        return _fail("guardian_blocked", g.get("reason", "Guardian blocked"), trace)
    if g["status"] in ("error", "unavailable") and not dry_run:
        return _fail("guardian_unavailable", g.get("reason", "Guardian unavailable; publish blocked"), trace)

    # ── Metabolism ──────────────────────────────────────────────────────
    m = await _metabolism_check(ws, content, risk_level=risk_level)
    trace.mark("metabolism", m["status"], error_code=m.get("error_code"), **{k: v for k, v in m.items() if k not in ("status", "error_code")})
    if m["status"] == "deny":
        return _fail(m.get("error_code", "metabolism_denied"), m.get("reason", "Metabolism denied"), trace)
    if m["status"] in ("unavailable", "error") and not dry_run:
        return _fail(m.get("error_code", "metabolism_unavailable"), m.get("reason", "Metabolism unavailable; publish blocked"), trace)

    # ── Council ─────────────────────────────────────────────────────────
    c = await _council_deliberate(ws, content, container, require=require_council)
    trace.mark("council", c["status"], error_code=c.get("error_code"), **{k: v for k, v in c.items() if k not in ("status", "error_code")})
    if c["status"] in ("veto", "reject", "deny", "block"):
        return _fail("council_veto", c.get("reason", "Council vetoed"), trace)
    if c["status"] in ("needs_revision", "escalated"):
        return _fail(
            "council_needs_revision",
            c.get("reason", "Council requires revision or escalation"),
            trace,
            decision_id=c.get("decision_id"),
        )
    if c["status"] == "unavailable" and require_council:
        return _fail("council_unavailable", "Council required but unavailable", trace)

    if c.get("decision_id") and not decision_id:
        decision_id = c["decision_id"]

    # ── Director / DecisionGate ─────────────────────────────────────────
    try:
        consume = (not dry_run) and (not durable or _from_outbox)
        decision = assert_publish_allowed(decision_id, ws, consume=consume)
        dec_id = getattr(decision, "decision_id", None) or decision_id or uuid.uuid4().hex
        trace.mark("director", "ok", decision_id=dec_id, consume=consume)
    except DecisionGateError as exc:
        trace.mark("director", "deny", error_code="decision_denied", error=str(exc))
        return _fail("decision_denied", str(exc), trace)
    except MetabolismError as exc:
        trace.mark("director", "deny", error_code=getattr(exc, "code", "metabolism"), error=str(exc))
        return _fail(getattr(exc, "code", "metabolism"), str(exc), trace)

    if dry_run:
        trace.mark("execute", "dry_run")
        return {
            "ok": True,
            "dry_run": True,
            "decision_id": dec_id,
            "workspace_id": ws,
            "brand_id": brand_id,
            "platform": platform,
            "trace": trace.as_list(),
            "correlation_id": trace.correlation_id,
        }

    # ── Outbox required when durable and not already from worker ────────
    if durable and not _from_outbox:
        try:
            from core.publish_outbox import publish_outbox
            item = publish_outbox.enqueue(
                workspace_id=ws,
                decision_id=dec_id,
                correlation_id=trace.correlation_id,
                brand_id=brand_id,
                platform=platform,
                caption=caption,
                topic=topic,
                risk_level=risk_level,
                payload={
                    **content,
                    **({"media_url": image_url} if image_url else {}),
                    **({"image_bytes_b64": base64.b64encode(image_bytes).decode("ascii")} if image_bytes else {}),
                    **({"scheduled_post_id": scheduled_post_id} if scheduled_post_id else {}),
                },
                automation_job_id=automation_job_id,
                idempotency_key=idempotency_key,
            )
            trace.mark("outbox", "queued", outbox_id=item.id)
            return {
                "ok": True,
                "queued": True,
                "outbox_id": item.id,
                "decision_id": dec_id,
                "workspace_id": ws,
                "trace": trace.as_list(),
                "correlation_id": trace.correlation_id,
            }
        except Exception as exc:
            trace.mark("outbox", "error", error_code="outbox_enqueue_failed", error=str(exc)[:300])
            return _fail("outbox_enqueue_failed", f"Durable outbox enqueue failed: {exc}", trace)

    # ── Connector / worker execution ────────────────────────────────────
    exec_result = await _connector_execute(
        container=container,
        workspace_id=ws,
        brand_id=brand_id,
        platform=platform,
        caption=caption,
        topic=topic,
        decision_id=dec_id,
        idempotency_key=idempotency_key,
        allow_simulate=bool(kwargs.get("allow_simulate", False)),
        image_url=image_url,
        image_bytes=image_bytes,
        brand=brand,
        to_number=to_number,
        recipient=recipient,
    )
    trace.mark(
        "connector",
        exec_result.get("status", "error"),
        error_code=exec_result.get("error_code"),
        **{k: v for k, v in exec_result.items() if k not in ("status", "error_code")},
    )

    execution_status = exec_result.get("status")
    platform_post_id = exec_result.get("platform_post_id")
    page_id = exec_result.get("page_id")
    account_id = exec_result.get("account_id")

    if execution_status == "ok" and not platform_post_id:
        logger.warning(
            "Pipeline: connector returned ok without platform_post_id "
            "decision=%s mode=%s — downgrading to uncertain",
            dec_id, exec_result.get("mode"),
        )
        exec_result = {
            **exec_result,
            "status": "uncertain",
            "error_code": "publish_no_receipt",
            "error": exec_result.get("error") or "Publish reported success without a post ID",
            "reconciliation_required": True,
        }
        execution_status = "uncertain"

    if execution_status == "uncertain":
        trace.mark(
            "outcome",
            "uncertain",
            platform_post_id=platform_post_id,
            page_id=page_id,
            account_id=account_id,
            reconciliation_required=True,
            execution_id=exec_result.get("execution_id"),
        )
        return _fail(
            "publish_uncertain",
            exec_result.get("error", "Provider result is uncertain; reconciliation required"),
            trace,
            decision_id=dec_id,
            execution_id=exec_result.get("execution_id"),
            platform_post_id=platform_post_id,
            page_id=page_id,
            account_id=account_id,
            mode=exec_result.get("mode", "live"),
            reconciliation_required=True,
        )

    outcome = "success" if execution_status == "ok" else "failure"

    try:
        decision_gate.record_outcome(
            dec_id,
            outcome=outcome,
            platform_post_id=platform_post_id,
            source="canonical_pipeline",
        )
        trace.mark("outcome", outcome, platform_post_id=platform_post_id)
    except Exception as exc:
        trace.mark("outcome", "error", error=str(exc)[:120])

    try:
        from core import memory as mem
        if hasattr(mem, "record_experience"):
            mem.record_experience(
                workspace_id=ws, kind=f"publish_{outcome}",
                content=content, detail={"decision_id": dec_id, "platform_post_id": platform_post_id},
                correlation_id=trace.correlation_id,
            )
        trace.mark("memory", "ok")
    except Exception as exc:
        trace.mark("memory", "degraded", error=str(exc)[:120])

    if outcome != "success":
        return _fail(
            "publish_failed",
            exec_result.get("error", "Publish failed"),
            trace,
            decision_id=dec_id,
            page_id=page_id,
            account_id=account_id,
            mode=exec_result.get("mode", "live"),
        )

    return {
        "ok": True,
        "status": "ok",
        "decision_id": dec_id,
        "workspace_id": ws,
        "brand_id": brand_id,
        "platform": platform,
        "platform_post_id": platform_post_id,
        "page_id": page_id,
        "account_id": account_id,
        "mode": exec_result.get("mode", "live"),
        "trace": trace.as_list(),
        "correlation_id": trace.correlation_id,
    }


def _fail(code: str, message: str, trace: PipelineTrace, **extra: Any) -> Dict[str, Any]:
    return {
        "ok": False,
        "code": code,
        "error": message,
        "trace": trace.as_list(),
        "correlation_id": trace.correlation_id,
        **extra,
    }


async def _guardian_evaluate(workspace_id: str, content: Dict[str, Any], container) -> Dict[str, Any]:
    try:
        if container is not None and hasattr(container, "try_resolve"):
            for name in ("GuardianService", "Guardian"):
                try:
                    pass
                except Exception:
                    pass
        try:
            from guardian import evaluate_action  # type: ignore
            result = evaluate_action(action="publish", workspace_id=workspace_id, payload=content)
            if isinstance(result, dict):
                status = str(result.get("status") or result.get("decision") or "allow").lower()
                return {"status": status, "reason": result.get("reason"), "mode": "guardian.evaluate_action"}
        except ImportError:
            pass
        try:
            from guardian.engine import GuardianEngine  # type: ignore
            eng = GuardianEngine()
            if hasattr(eng, "evaluate"):
                result = eng.evaluate(action="publish", workspace_id=workspace_id, payload=content)
                if isinstance(result, dict):
                    status = str(result.get("status") or "allow").lower()
                    return {"status": status, "mode": "GuardianEngine"}
        except Exception:
            pass
    except Exception as exc:
        logger.debug("guardian evaluate: %s", exc)
        return {"status": "error", "error_code": "guardian_error", "reason": str(exc)[:200]}

    return {"status": "unavailable", "mode": "guardian_unavailable", "reason": "Guardian not bound — live publish blocked"}


async def _metabolism_check(workspace_id: str, content: Dict[str, Any], risk_level: str = "low") -> Dict[str, Any]:
    try:
        from core.metabolism import Metabolism, MetabolismError, metabolism

        if metabolism is not None and hasattr(metabolism, "assert_can_publish"):
            metabolism.assert_can_publish(workspace_id)
            return {"status": "ok", "via": "metabolism.assert_can_publish"}

        instance = Metabolism()
        instance.assert_can_publish(workspace_id)
        return {"status": "ok", "via": "Metabolism.assert_can_publish"}

    except MetabolismError as exc:
        return {"status": "deny", "error_code": getattr(exc, "code", "metabolism_quota_exceeded"), "reason": str(exc)[:200]}
    except Exception as exc:
        logger.warning("Metabolism check failed: %s", exc)
        return {"status": "unavailable", "error_code": "metabolism_unavailable", "reason": str(exc)[:200]}


async def _council_deliberate(
    workspace_id: str, content: Dict[str, Any], container, *, require: bool
) -> Dict[str, Any]:
    try:
        from council.orchestration.council_orchestrator import CouncilOrchestrator
        from council.models import Proposal
    except Exception:
        Proposal = None
        CouncilOrchestrator = None

    orch = None
    if container is not None:
        try:
            if hasattr(container, "try_resolve") and CouncilOrchestrator:
                orch = container.try_resolve(CouncilOrchestrator)
        except Exception:
            pass

    if orch is None:
        if require:
            return {"status": "unavailable", "error_code": "council_unavailable"}
        return {"status": "skipped", "reason": "no_bound_council", "mode": "optional"}

    try:
        description = ""
        if isinstance(content, dict):
            description = str(
                content.get("caption")
                or content.get("text")
                or content.get("body")
                or content.get("topic")
                or ""
            )[:500]
        if Proposal is not None:
            try:
                proposal = Proposal(
                    action_name="publish",
                    source_engine="canonical_pipeline",
                    proposal_type="publish",
                    description=description or "publish content",
                    metadata={"workspace_id": workspace_id, "content": content},
                )
            except TypeError:
                proposal = Proposal(
                    action_name="publish",
                    source_engine="canonical_pipeline",
                    proposal_type="publish",
                    workspace_id=workspace_id,
                    payload=content,
                )
        else:
            proposal = type("P", (), {
                "id": "pub_duck",
                "action_name": "publish",
                "source_engine": "canonical_pipeline",
                "proposal_type": "publish",
                "description": description or "publish content",
                "workspace_id": workspace_id,
                "payload": content,
            })()

        result = await orch.deliberate(proposal)
        decision = getattr(result, "decision", None) or getattr(result, "status", None) or result
        if hasattr(decision, "value"):
            decision = decision.value
        status = str(decision).lower().replace("decisionstate.", "")
        reason = str(
            getattr(result, "reason", None)
            or (getattr(result, "warnings", None) or [None])[0]
            or status
        )

        if status in (
            "reject", "rejected", "veto", "deny", "denied",
            "block", "blocked", "escalated", "needs_revision", "needsrevision",
        ):
            return {
                "status": "veto" if status in ("reject", "rejected", "veto", "deny", "denied", "block", "blocked") else "needs_revision",
                "reason": reason,
                "decision": status,
            }

        decision_id = None
        try:
            from core.decision_gate import decision_gate, DecisionSource
            approved = status in (
                "approved", "approve", "ok", "approved_with_warnings", "approvedwithwarnings",
            )
            issued = decision_gate.issue(
                source=DecisionSource.COUNCIL if hasattr(DecisionSource, "COUNCIL") else list(DecisionSource)[0],
                workspace_id=workspace_id or "platform",
                action_name="publish",
                approved=approved,
                risk_level=str(getattr(result, "risk_level", "medium")),
                reason=reason[:300],
                votes=[
                    {
                        "member": getattr(v, "member", None),
                        "vote": getattr(v, "vote", None),
                        "confidence": getattr(v, "confidence", None),
                    }
                    for v in (getattr(result, "votes", None) or [])
                ],
                metadata={"council_status": status},
            )
            decision_id = issued.decision_id
        except Exception as gate_exc:
            logger.warning("decision_gate.issue failed after council: %s", gate_exc)

        if not status.startswith("approv") and status not in ("ok", "approved_with_warnings"):
            if decision_id is None:
                return {"status": "needs_revision", "reason": reason, "decision": status}

        return {
            "status": "ok",
            "decision": status,
            "decision_id": decision_id,
            "reason": reason,
        }
    except Exception as exc:
        logger.warning("council deliberate failed: %s", exc)
        if require:
            return {"status": "unavailable", "error_code": "council_error", "reason": str(exc)[:200]}
        return {"status": "skipped", "reason": str(exc)[:200], "mode": "error_optional"}


async def _connector_execute(
    *,
    container,
    workspace_id,
    brand_id,
    platform,
    caption,
    topic,
    decision_id,
    idempotency_key: Optional[str] = None,
    allow_simulate: bool,
    image_url: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
    brand: Optional[Any] = None,
    to_number: Optional[str] = None,
    recipient: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve a live connector, execute the publish, and return a receipt.

    See module docstring for the receipt contract.

    DIAG: emits WARNING-level log lines at resolution, plugin call, and exit.
    """
    text = (caption or topic or "").strip() or f"Kanyoza {decision_id[:8]}"

    # ═══════════════════════════════════════════════════════════════════
    # DIAG: entry
    # ═══════════════════════════════════════════════════════════════════
    logger.warning(
        "DIAG _connector_execute: ENTER platform=%s brand_id=%s decision=%s "
        "container=%s allow_simulate=%s",
        platform, brand_id, decision_id,
        "PRESENT" if container is not None else "NONE",
        allow_simulate,
    )
    if container is not None:
        social = getattr(container, "social_plugins", None)
        industry = getattr(container, "industry_plugins", None)
        logger.warning(
            "DIAG _connector_execute: container.social_plugins keys=%s "
            "industry_plugins keys=%s",
            list(social.keys()) if isinstance(social, dict) else repr(social),
            list(industry.keys()) if isinstance(industry, dict) else repr(industry),
        )

    # ── Resolve a live connector ────────────────────────────────────────
    plugin = None
    page_id = None
    account_id = None

    try:
        if container is not None:
            social = getattr(container, "social_plugins", None) or {}
            industry = getattr(container, "industry_plugins", None) or {}
            plugin = social.get(platform) or industry.get(platform)
            if plugin is None and platform == "facebook":
                plugin = social.get("facebook") or industry.get("facebook")

            logger.warning(
                "DIAG _connector_execute: initial plugin=%s",
                type(plugin).__name__ if plugin else None,
            )

            if brand_id:
                try:
                    from repositories.social_account_repository import SocialAccountRepository
                    repo = None
                    from repositories.social_account_repository import SocialAccountRepository as SAR
                    if hasattr(container, "try_resolve"):
                        candidate = container.try_resolve(SAR)
                        if isinstance(candidate, SAR):
                            repo = candidate
                    if repo is None:
                        repo = SAR()

                    logger.warning(
                        "DIAG _connector_execute: looking up account brand_id=%s platform=%s",
                        brand_id, platform,
                    )
                    account = repo.find_by_brand_and_platform(str(brand_id), str(platform))
                    logger.warning(
                        "DIAG _connector_execute: account lookup result=%r",
                        account,
                    )
                    if account is not None:
                        from services.connectors.connector_factory import create_connector
                        plugin = create_connector(account)
                        page_id = getattr(account, "platform_account_id", None)
                        account_id = getattr(account, "id", None)
                        logger.warning(
                            "DIAG _connector_execute: created plugin=%s page_id=%s account_id=%s",
                            type(plugin).__name__ if plugin else None,
                            page_id, account_id,
                        )
                except Exception as account_exc:
                    logger.exception(
                        "DIAG _connector_execute: account resolution FAILED platform=%s brand=%s",
                        platform, brand_id,
                    )
                    try:
                        from config.settings import settings
                        if getattr(settings, "is_production", False):
                            return {
                                "status": "error",
                                "error_code": "social_account_resolution_failed",
                                "error": "Durable social account resolution is required before external publishing",
                                "mode": "blocked",
                            }
                    except Exception:
                        pass
    except Exception as exc:
        logger.exception("DIAG _connector_execute: connector resolution raised")
        return {
            "status": "error",
            "error_code": "connector_resolution_failed",
            "error": str(exc)[:300],
            "mode": "live",
        }

    logger.warning(
        "DIAG _connector_execute: final plugin=%s has_publish=%s page_id=%s account_id=%s",
        type(plugin).__name__ if plugin else None,
        bool(plugin and hasattr(plugin, "publish")),
        page_id, account_id,
    )

    # ── No connector bound → explicit error or dev-only simulation ──────
    if not (plugin and hasattr(plugin, "publish")):
        if allow_simulate:
            logger.warning(
                "DIAG _connector_execute: SIMULATING decision=%s platform=%s",
                decision_id, platform,
            )
            return {
                "status": "uncertain",
                "error_code": "simulated_publish",
                "error": "No live connector bound; simulation must not be treated as success",
                "mode": "simulated",
                "platform_post_id": None,
                "reconciliation_required": True,
            }
        logger.warning(
            "DIAG _connector_execute: EXIT connector_unavailable decision=%s",
            decision_id,
        )
        return {
            "status": "error",
            "error_code": "connector_unavailable",
            "error": "No connector bound and simulation disabled",
            "mode": "none",
        }

    # ── Execute through the durable ledger ──────────────────────────────
    ledger = None
    execution = None
    if idempotency_key:
        try:
            from services.execution_ledger import ExecutionLedger
            db = None
            try:
                from supabase import Client as SupabaseClient
                if hasattr(container, "try_resolve"):
                    db = container.try_resolve(SupabaseClient)
            except Exception:
                db = None
            ledger = ExecutionLedger(db=db)
            execution = ledger.begin(
                workspace_id=str(workspace_id), action="publish",
                idempotency_key=idempotency_key, provider=str(platform),
                metadata={"decision_id": decision_id, "brand_id": brand_id},
            )
            if execution.status in {"succeeded", "uncertain"}:
                logger.warning(
                    "DIAG _connector_execute: ledger_replay status=%s execution=%s",
                    execution.status, execution.execution_id,
                )
                return {
                    "status": "ok" if execution.status == "succeeded" else "uncertain",
                    "platform_post_id": execution.provider_object_id,
                    "page_id": page_id,
                    "account_id": account_id,
                    "mode": "ledger_replay",
                    "execution_id": execution.execution_id,
                    "reconciliation_required": execution.status == "uncertain",
                }
        except Exception as ledger_exc:
            logger.exception("DIAG _connector_execute: ledger.begin failed")
            try:
                from config.settings import settings
                if getattr(settings, "is_production", False):
                    return {
                        "status": "error",
                        "error_code": "execution_ledger_unavailable",
                        "error": "Durable execution ledger is required before external publishing",
                        "mode": "blocked",
                    }
            except ImportError:
                pass

    try:
        if ledger and execution:
            from services.external_action_executor import ExternalActionExecutor
            executor = ExternalActionExecutor(ledger)

            logger.warning(
                "DIAG _connector_execute: CALLING plugin.publish via executor "
                "plugin=%s page_id=%s account_id=%s execution=%s",
                type(plugin).__name__, page_id, account_id,
                execution.execution_id if execution else None,
            )

            exec_result = await executor.execute(
                workspace_id=str(workspace_id),
                action="publish",
                idempotency_key=idempotency_key,
                provider=str(platform),
                operation=lambda: plugin.publish(
                    text, image_url=image_url, image_bytes=image_bytes,
                    idempotency_key=idempotency_key, brand=brand,
                    **({"to_number": to_number} if to_number else {}),
                    **({"recipient": recipient} if recipient else {}),
                ),
                metadata={"decision_id": decision_id, "brand_id": brand_id},
            )

            logger.warning(
                "DIAG _connector_execute: executor result status=%s error=%r "
                "provider_object_id=%r execution_id=%r",
                exec_result.get("status"),
                exec_result.get("error"),
                exec_result.get("provider_object_id"),
                exec_result.get("execution_id"),
            )

            status = exec_result.get("status")
            if status == "succeeded":
                return {
                    "status": "ok",
                    "platform_post_id": exec_result.get("provider_object_id"),
                    "page_id": page_id,
                    "account_id": account_id,
                    "mode": "live",
                    "execution_id": exec_result.get("execution_id"),
                }
            if status == "uncertain":
                return {
                    "status": "uncertain",
                    "error_code": "external_side_effect_uncertain",
                    "error": exec_result.get("error", "Provider result is uncertain"),
                    "page_id": page_id,
                    "account_id": account_id,
                    "mode": "live",
                    "execution_id": exec_result.get("execution_id"),
                    "reconciliation_required": True,
                }
            return {
                "status": "error",
                "error": exec_result.get("error", "External publish failed"),
                "page_id": page_id,
                "account_id": account_id,
                "mode": "live",
                "execution_id": exec_result.get("execution_id"),
            }

        # Non-ledger fallback path (development / non-durable callers)
        logger.warning(
            "DIAG _connector_execute: FALLBACK direct plugin.publish (no ledger) "
            "plugin=%s page_id=%s account_id=%s",
            type(plugin).__name__, page_id, account_id,
        )
        result = plugin.publish(
            text,
            image_url=image_url,
            image_bytes=image_bytes,
            idempotency_key=idempotency_key,
            brand=brand,
        )
        logger.warning(
            "DIAG _connector_execute: fallback result=%r",
            result if isinstance(result, dict) else repr(result),
        )
        ok = bool(result.get("ok", True)) if isinstance(result, dict) else bool(getattr(result, "ok", True))
        post_id = (
            (result.get("post_id") or result.get("id") or result.get("provider_object_id"))
            if isinstance(result, dict)
            else (getattr(result, "post_id", None) or getattr(result, "provider_object_id", None))
        )
        error = result.get("error") if isinstance(result, dict) else getattr(result, "error", None)
        outcome = (
            result.get("outcome") if isinstance(result, dict) else getattr(result, "outcome", None)
        )
        outcome = str(outcome or "").lower()

        if outcome == "uncertain" or (ok and not post_id):
            return {
                "status": "uncertain",
                "error_code": "external_side_effect_uncertain",
                "error": error or "Provider returned success without a post ID",
                "page_id": page_id,
                "account_id": account_id,
                "mode": "live",
                "platform_post_id": None,
                "reconciliation_required": True,
            }
        if ok and post_id:
            return {
                "status": "ok",
                "platform_post_id": post_id,
                "page_id": page_id,
                "account_id": account_id,
                "mode": "live",
            }
        return {
            "status": "error",
            "error": error or "Publish failed",
            "page_id": page_id,
            "account_id": account_id,
            "mode": "live",
        }

    except Exception as exc:
        logger.exception(
            "DIAG _connector_execute: EXCEPTION during plugin.publish path "
            "plugin=%s page_id=%s account_id=%s",
            type(plugin).__name__ if plugin else None, page_id, account_id,
        )
        if ledger and execution:
            try:
                ledger.complete(
                    execution,
                    status="uncertain",
                    error=str(exc)[:300],
                    evidence={
                        "mode": "live",
                        "platform": platform,
                        "reconcile_required": True,
                        "exception_type": type(exc).__name__,
                    },
                )
            except Exception:
                logger.exception("DIAG _connector_execute: ledger.complete failed")
        return {
            "status": "uncertain",
            "error_code": "external_side_effect_uncertain",
            "error": str(exc)[:300],
            "page_id": page_id,
            "account_id": account_id,
            "mode": "live",
            "reconciliation_required": True,
        }
