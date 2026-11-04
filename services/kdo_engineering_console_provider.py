"""Explicit PostgreSQL provider for the KDO engineering console.

This provider persists task intake, plan submissions and review decisions. It does
not pretend that dynamic agent planning or sandbox execution is available: those
capabilities remain false until a real worker/orchestrator is injected separately.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from services.kdo_engineering_phase16_5 import PostgresEngineeringTaskRepository, Phase165Error
from services.kdo_engineering_task_repository import EngineeringTaskConflict, EngineeringTaskNotFound
from services.kdo_engineering_vertical_contracts import EngineeringTaskContract, EngineeringTaskStatus


class EngineeringConsoleProviderError(RuntimeError):
    pass


def _active_agents_for_job(job: dict[str, Any] | None, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Project only a currently leased worker as active; stale leases are not activity."""
    if not job or job.get("status") != "running" or not job.get("lease_owner"):
        return []
    expiry = job.get("lease_expires_at")
    if isinstance(expiry, str):
        try:
            expiry = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        except ValueError:
            return []
    if not isinstance(expiry, datetime):
        return []
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if expiry <= current:
        return []
    return [{
        "agent_id": str(job["lease_owner"]),
        "name": str(job["lease_owner"]),
        "role": "Build Worker",
        "status": "running",
        "activity": "Executing a lease-held engineering build job",
        "job_id": job.get("job_id"),
        "lease_expires_at": expiry.isoformat(),
    }]


def _task_payload(task: EngineeringTaskContract, *, build_job: dict[str, Any] | None = None) -> dict[str, Any]:
    evidence = dict(task.evidence or {})
    return {
        "task_id": task.task_id,
        "tenant_id": task.tenant_id,
        "workspace_id": task.workspace_id,
        "objective": task.objective,
        "acceptance_criteria": list(task.acceptance_criteria),
        "idempotency_key": task.idempotency_key,
        "status": task.status.value,
        "revision": task.revision,
        "repository_ref": task.repository_ref,
        "base_revision": task.base_revision,
        "metadata": dict(task.metadata or {}),
        "evidence": evidence,
        "approval_required": task.status == EngineeringTaskStatus.PROPOSED and evidence.get("plan_review", {}).get("state") == "submitted",
        "build_job": build_job,
        "active_agents": _active_agents_for_job(build_job),
        "created_at": None,
        "updated_at": None,
    }


class PostgresEngineeringConsoleProvider:
    """Workspace-scoped console provider using the Phase 16.5 PostgreSQL task store."""

    def __init__(self, connection_factory: Callable[[], Any], *, plan_generator: Callable[..., dict[str, Any]] | None = None):
        if not callable(connection_factory):
            raise ValueError("postgres_connection_factory_required")
        self._connect = connection_factory
        self.tasks = PostgresEngineeringTaskRepository(connection_factory)
        self._plan_generator = plan_generator if callable(plan_generator) else None

    def _append_event(self, *, task_id: str, workspace_id: str, event_type: str,
                      actor_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        conn = cur = None
        try:
            conn = self._connect()
            cur = conn.cursor()
            # Serialize sequence allocation per task; unique constraints remain the
            # final guard against concurrent writers and transaction retries.
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{workspace_id}:{task_id}",))
            cur.execute("""SELECT sequence_no,event_hash FROM public.kdo_engineering_console_events
                WHERE tenant_id=%s AND workspace_id=%s AND task_id=%s
                ORDER BY sequence_no DESC LIMIT 1 FOR UPDATE""", (workspace_id, workspace_id, task_id))
            previous = cur.fetchone()
            seq = 1 if previous is None else int(previous[0]) + 1
            previous_hash = "GENESIS" if previous is None else str(previous[1])
            event_id = uuid.uuid4().hex
            created_at = datetime.now(timezone.utc)
            body = {"event_id": event_id, "task_id": task_id, "tenant_id": workspace_id,
                    "workspace_id": workspace_id, "sequence_no": seq, "event_type": event_type,
                    "actor_id": actor_id, "payload_json": encoded, "created_at": created_at.isoformat(),
                    "previous_hash": previous_hash}
            digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            cur.execute("""INSERT INTO public.kdo_engineering_console_events
                (event_id,task_id,tenant_id,workspace_id,sequence_no,event_type,actor_id,payload,
                 created_at,previous_hash,event_hash)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)""",
                (event_id, task_id, workspace_id, workspace_id, seq, event_type, actor_id,
                 encoded, created_at, previous_hash, digest))
            conn.commit()
            return {**body, "event_hash": digest, "payload": payload}
        except Exception as exc:
            if conn is not None:
                try: conn.rollback()
                except Exception: pass
            raise EngineeringConsoleProviderError("engineering_event_persistence_failed") from exc
        finally:
            if cur is not None:
                try: cur.close()
                except Exception: pass
            if conn is not None:
                try: conn.close()
                except Exception: pass

    def capabilities(self, *, workspace_id: str) -> dict[str, Any]:
        if not workspace_id:
            raise EngineeringConsoleProviderError("workspace_scope_required")
        db_ok = False
        conn = cur = None
        try:
            conn = self._connect(); cur = conn.cursor(); cur.execute("SELECT 1"); db_ok = cur.fetchone()[0] == 1
        except Exception:
            db_ok = False
        finally:
            if cur is not None:
                try: cur.close()
                except Exception: pass
            if conn is not None:
                try: conn.close()
                except Exception: pass
        return {
            "provider_configured": True,
            "runtime_connected": False,
            "database_connected": db_ok,
            "task_persistence": True,
            "persisted_events": True,
            "authoritative_build_job_projection": True,
            "lease_aware_worker_presence": True,
            "plan_submission": True,
            "governed_plan_review": True,
            "dynamic_agent_planning": self._plan_generator is not None,
            "sandbox_execution": False,
            "automatic_repair": False,
            "deployment_authority": False,
            "agent_count": 0,
            "planner_configured": self._plan_generator is not None,
            "workspace_id": workspace_id,
            "limitations": [
                *([] if self._plan_generator is not None else ["Dynamic agent planning is not connected to this provider."]),
                "Build/test execution and automatic repair remain disabled until a governed isolated worker is configured.",
            ],
        }

    def _load_build_jobs(self, *, tenant_id: str, workspace_id: str,
                         task_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Read authoritative queue/lease state for the task board in one scoped query."""
        if not task_ids:
            return {}
        conn = cur = None
        try:
            conn = self._connect()
            cur = conn.cursor()
            cur.execute("""SELECT job_id,task_id,status,attempts,lease_owner,lease_expires_at,
                                  created_at,updated_at
                FROM public.kdo_engineering_build_jobs
                WHERE tenant_id=%s AND workspace_id=%s AND task_id = ANY(%s)""",
                (tenant_id, workspace_id, task_ids))
            result = {}
            for row in cur.fetchall():
                job_id, task_id, status, attempts, lease_owner, lease_expires_at, created_at, updated_at = row
                result[str(task_id)] = {
                    "job_id": str(job_id), "task_id": str(task_id), "status": str(status),
                    "attempts": int(attempts), "lease_owner": lease_owner,
                    "lease_expires_at": lease_expires_at.isoformat() if hasattr(lease_expires_at, "isoformat") else lease_expires_at,
                    "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
                    "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else str(updated_at),
                }
            return result
        except Exception as exc:
            raise EngineeringConsoleProviderError("engineering_build_job_projection_unavailable") from exc
        finally:
            if cur is not None:
                try: cur.close()
                except Exception: pass
            if conn is not None:
                try: conn.close()
                except Exception: pass

    def list_tasks(self, *, workspace_id: str, limit: int) -> dict[str, Any]:
        items = self.tasks.list_scope(tenant_id=workspace_id, workspace_id=workspace_id, limit=min(max(int(limit), 1), 100))
        jobs = self._load_build_jobs(tenant_id=workspace_id, workspace_id=workspace_id,
                                     task_ids=[item.task_id for item in items])
        return {"tasks": [_task_payload(item, build_job=jobs.get(item.task_id)) for item in items]}

    def submit_task(self, *, workspace_id: str, user_id: str, objective: str,
                    acceptance_criteria: list[str], idempotency_key: str | None) -> dict[str, Any]:
        if not workspace_id or not user_id:
            raise EngineeringConsoleProviderError("authenticated_workspace_and_user_required")
        criteria = tuple(acceptance_criteria or ["Deliver the requested change and report verifiable acceptance evidence."])
        key = (idempotency_key or "").strip() or uuid.uuid4().hex
        task = EngineeringTaskContract(
            task_id=uuid.uuid4().hex,
            tenant_id=workspace_id,
            workspace_id=workspace_id,
            objective=objective,
            acceptance_criteria=criteria,
            idempotency_key=key,
            status=EngineeringTaskStatus.PROPOSED,
            metadata={"submitted_by": user_id, "intake_source": "kdo_control_plane"},
        )
        stored = self.tasks.create(task)
        # Repository idempotency can return an existing task. Do not append a
        # duplicate submission event for a retry of the same request.
        existing_events = self.list_events(task_id=stored.task_id, workspace_id=workspace_id, after_sequence=0, limit=500)["events"]
        if stored.task_id == task.task_id or not any(event.get("event_type") == "task_submitted" for event in existing_events):
            self._append_event(task_id=stored.task_id, workspace_id=workspace_id,
                event_type="task_submitted", actor_id=user_id,
                payload={"objective": stored.objective, "revision": stored.revision,
                         "status": stored.status.value, "acceptance_criteria": list(stored.acceptance_criteria)})
        return _task_payload(stored)

    def get_task(self, *, task_id: str, workspace_id: str) -> dict[str, Any] | None:
        try:
            task = self.tasks.get(task_id, tenant_id=workspace_id, workspace_id=workspace_id)
        except EngineeringTaskNotFound:
            return None
        jobs = self._load_build_jobs(tenant_id=workspace_id, workspace_id=workspace_id, task_ids=[task_id])
        return _task_payload(task, build_job=jobs.get(task_id))

    def list_events(self, *, task_id: str, workspace_id: str, after_sequence: int, limit: int) -> dict[str, Any]:
        conn = cur = None
        try:
            conn = self._connect(); cur = conn.cursor()
            cur.execute("""SELECT event_id,task_id,sequence_no,event_type,actor_id,payload,created_at,previous_hash,event_hash
                FROM public.kdo_engineering_console_events WHERE tenant_id=%s AND workspace_id=%s AND task_id=%s
                AND sequence_no>%s ORDER BY sequence_no LIMIT %s""",
                (workspace_id, workspace_id, task_id, max(0, after_sequence), min(max(limit, 1), 500)))
            rows = cur.fetchall()
            events = []
            for row in rows:
                payload = row[5] if isinstance(row[5], dict) else json.loads(row[5] or "{}")
                events.append({"event_id": row[0], "task_id": row[1], "sequence_no": row[2],
                    "event_type": row[3], "actor_id": row[4], "payload": payload,
                    "created_at": row[6].isoformat() if hasattr(row[6], "isoformat") else str(row[6]),
                    "previous_hash": row[7], "event_hash": row[8]})
            return {"events": events}
        except Exception as exc:
            raise EngineeringConsoleProviderError("engineering_event_read_failed") from exc
        finally:
            if cur is not None:
                try: cur.close()
                except Exception: pass
            if conn is not None:
                try: conn.close()
                except Exception: pass

    def generate_plan(self, *, task_id: str, workspace_id: str) -> dict[str, Any]:
        if self._plan_generator is None:
            raise EngineeringConsoleProviderError("dynamic_planner_not_configured")
        task = self.tasks.get(task_id, tenant_id=workspace_id, workspace_id=workspace_id)
        if task.status != EngineeringTaskStatus.PROPOSED:
            raise EngineeringConsoleProviderError("planning_requires_proposed_task")
        if task.evidence.get("plan_review", {}).get("state") in {"submitted", "approved", "rejected"}:
            raise EngineeringConsoleProviderError("plan_already_submitted")
        try:
            proposal = self._plan_generator(task={"task_id": task.task_id, "objective": task.objective,
                "acceptance_criteria": list(task.acceptance_criteria)}, workspace_id=workspace_id)
        except Exception as exc:
            # Keep the API response generic, but preserve the planner's bounded
            # failure code in server logs for diagnosis without logging prompts
            # or model output.
            logging.getLogger(__name__).warning(
                "Engineering planner proposal failed: %s", str(exc)[:180]
            )
            raise EngineeringConsoleProviderError("dynamic_planning_failed") from exc
        if not isinstance(proposal, dict) or not isinstance(proposal.get("steps"), list):
            raise EngineeringConsoleProviderError("invalid_planner_response")
        return {"task_id": task.task_id, "revision": task.revision, **proposal}

    def submit_plan(self, *, task_id: str, workspace_id: str, user_id: str, steps: list[str], assignments: list[dict[str, Any]] | None = None, summary: str | None = None, planner: dict[str, Any] | None = None) -> dict[str, Any]:
        if not steps or any(not isinstance(step, str) or not step.strip() for step in steps):
            raise ValueError("plan_must_contain_nonempty_steps")
        task = self.tasks.get(task_id, tenant_id=workspace_id, workspace_id=workspace_id)
        if task.evidence.get("plan_review", {}).get("state") in {"submitted", "approved", "rejected"}:
            raise EngineeringConsoleProviderError("plan_already_submitted")
        if task.status != EngineeringTaskStatus.PROPOSED:
            raise EngineeringConsoleProviderError("plan_submission_requires_proposed_task")
        normalized = [step.strip() for step in steps]
        normalized_assignments = None
        if assignments is not None:
            try:
                from services.kdo_engineering_phase16_11_planner import _validate_assignments
                normalized_assignments = _validate_assignments(assignments)
            except Exception as exc:
                raise EngineeringConsoleProviderError("invalid_specialist_assignments") from exc
        if summary is not None and (not isinstance(summary, str) or len(summary) > 4000):
            raise EngineeringConsoleProviderError("invalid_plan_summary")
        if planner is not None and not isinstance(planner, dict):
            raise EngineeringConsoleProviderError("invalid_planner_evidence")
        digest = hashlib.sha256(json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        plan_review = {"state": "submitted", "steps": normalized,
            "digest": digest, "submitted_by": user_id}
        if normalized_assignments is not None:
            plan_review["assignments"] = normalized_assignments
            plan_review["assignments_digest"] = hashlib.sha256(
                json.dumps(normalized_assignments, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":")).encode("utf-8")
            ).hexdigest()
        if summary:
            plan_review["summary"] = summary.strip()
        if planner:
            # Planner metadata is retained as unverified proposal evidence.
            plan_review["planner"] = {**planner, "trust": "model_proposal_unverified"}
        updated = self.tasks.update_evidence(task_id, tenant_id=workspace_id, workspace_id=workspace_id,
            expected_revision=task.revision, evidence={"plan_review": plan_review})
        self._append_event(task_id=task_id, workspace_id=workspace_id, event_type="plan_submitted", actor_id=user_id,
            payload={"revision": updated.revision, "step_count": len(normalized), "plan_digest": digest})
        return _task_payload(updated)

    def decide(self, *, task_id: str, workspace_id: str, user_id: str, decision: str,
               expected_revision: int) -> dict[str, Any]:
        if decision not in {"approve", "reject"}:
            raise ValueError("invalid_decision")
        updated = self.tasks.review_plan_atomically(task_id, tenant_id=workspace_id, workspace_id=workspace_id,
            expected_revision=expected_revision, reviewer_id=user_id, approve=(decision == "approve"),
            reason="Reviewed through KDO Control Plane")
        self._append_event(task_id=task_id, workspace_id=workspace_id, event_type=f"plan_{decision}d", actor_id=user_id,
            payload={"revision": updated.revision, "status": updated.status.value, "decision": decision})
        return {**_task_payload(updated), "decision": decision, "status": updated.status.value}


def create_console_provider_from_environment() -> PostgresEngineeringConsoleProvider | None:
    """Configure only when an explicit dedicated engineering DSN is supplied."""
    dsn = os.getenv("KDO_ENGINEERING_DATABASE_URL", "").strip()
    if not dsn:
        return None
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("KDO_ENGINEERING_DATABASE_URL_requires_psycopg3") from exc
    plan_generator = None
    try:
        from ai.gateway import get_llm_gateway
        from services.kdo_engineering_phase16_11_planner import make_engineering_plan_generator
        gateway = get_llm_gateway()
        if callable(getattr(gateway, "complete_structured", None)):
            plan_generator = make_engineering_plan_generator(gateway)
    except Exception:
        # Optional planning remains truthfully unavailable; do not break app boot.
        plan_generator = None
    return PostgresEngineeringConsoleProvider(lambda: psycopg.connect(dsn, connect_timeout=5), plan_generator=plan_generator)
