"""Append-only persistence for Director engineering change evidence.

This ledger records source facts observed from GitHub and Render. It never
triggers repository mutations, deployments, migrations, or runtime actions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _event_time(value: Any) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def events_from_snapshot(snapshot: dict) -> list[dict]:
    """Build deterministic, deduplicated ledger events from one evidence snapshot."""
    events: dict[str, dict] = {}
    observed_at = datetime.now(timezone.utc).isoformat()
    for repository in snapshot.get("repositories", []):
        if not isinstance(repository, dict):
            continue
        repo = str(repository.get("repository") or "")[:300]
        for pull in repository.get("merged_pull_requests", []):
            if not isinstance(pull, dict) or not pull.get("number"):
                continue
            number = str(pull["number"])
            merge_key = f"github:pr:{repo}#{number}"
            common = {
                "repository": repo,
                "event_at": _event_time(pull.get("merged_at")),
                "observed_at": observed_at,
                "commit_sha": pull.get("merge_commit_sha") or pull.get("head_sha"),
                "evidence_source": "github_pull_request",
            }
            events[merge_key] = {
                **common,
                "event_key": merge_key,
                "event_type": "pull_request_merged",
                "source_id": number,
                "payload": {
                    "number": pull.get("number"),
                    "title": pull.get("title"),
                    "url": pull.get("url"),
                    "base_branch": pull.get("base_branch"),
                    "changed_files": pull.get("changed_files"),
                    "additions": pull.get("additions"),
                    "deletions": pull.get("deletions"),
                },
            }
            for path in pull.get("changed_paths", []) or []:
                path = str(path)[:500]
                if not (path.startswith("migrations/") or "/migrations/" in path):
                    continue
                key = f"github:migration:{repo}#{number}:{path}"
                events[key] = {
                    **common,
                    "event_key": key,
                    "event_type": "database_migration_merged",
                    "source_id": f"{number}:{path}",
                    "payload": {
                        "pull_request_number": pull.get("number"),
                        "pull_request_title": pull.get("title"),
                        "pull_request_url": pull.get("url"),
                        "migration_path": path,
                        "migration_state": "merged_not_applied",
                    },
                }
        workflow = repository.get("latest_workflow")
        if isinstance(workflow, dict) and workflow.get("id"):
            run_id = str(workflow["id"])
            key = f"github:workflow:{repo}:{run_id}"
            events[key] = {
                "event_key": key,
                "event_type": "workflow_run",
                "repository": repo,
                "source_id": run_id,
                "event_at": _event_time(workflow.get("updated_at")),
                "observed_at": observed_at,
                "commit_sha": workflow.get("head_sha"),
                "evidence_source": "github_actions",
                "payload": {
                    "run_id": workflow.get("id"),
                    "name": workflow.get("name"),
                    "status": workflow.get("status"),
                    "conclusion": workflow.get("conclusion"),
                    "url": workflow.get("url"),
                    "correlated_to_merge": False,
                },
            }
    render = snapshot.get("render") or {}
    for deployment in render.get("deployments", []) or []:
        if not isinstance(deployment, dict) or not deployment.get("deploy_id"):
            continue
        deploy_id = str(deployment["deploy_id"])
        key = f"render:deployment:{deploy_id}"
        events[key] = {
            "event_key": key,
            "event_type": "deployment",
            "repository": deployment.get("repository"),
            "source_id": deploy_id,
            "event_at": _event_time(deployment.get("finished_at") or deployment.get("created_at")),
            "observed_at": observed_at,
            "commit_sha": deployment.get("commit_sha"),
            "evidence_source": "render_deployment",
            "payload": {
                "deploy_id": deploy_id,
                "service_id": deployment.get("service_id"),
                "service_name": deployment.get("service_name"),
                "status": deployment.get("status"),
                "url": deployment.get("url"),
            },
        }
    return list(events.values())


async def persist_engineering_snapshot(snapshot: dict, *, client=None, limit: int = 100) -> dict:
    """Idempotently append observed source events and return the durable timeline."""
    events = events_from_snapshot(snapshot)
    try:
        if client is None:
            from services.supabase import get_service_client_from_settings
            client = get_service_client_from_settings()
        if events:
            response = await __import__("asyncio").to_thread(
                lambda: client.table("director_engineering_events")
                .upsert(events, on_conflict="event_key", ignore_duplicates=True)
                .execute()
            )
            inserted = len(getattr(response, "data", None) or [])
        else:
            inserted = 0
        result = await __import__("asyncio").to_thread(
            lambda: client.table("director_engineering_events")
            .select("*").order("event_at", desc=True).limit(max(1, min(int(limit), 200))).execute()
        )
        rows = [dict(row) for row in (getattr(result, "data", None) or [])]
        return {
            "status": "available",
            "event_count_observed": len(events),
            "new_events_persisted": inserted,
            "returned_events": len(rows),
            "events": rows,
            "append_only": True,
            "runtime_verification_is_separate": True,
        }
    except Exception as exc:
        return {
            "status": "unavailable",
            "event_count_observed": len(events),
            "new_events_persisted": 0,
            "returned_events": 0,
            "events": [],
            "append_only": True,
            "runtime_verification_is_separate": True,
            "error": type(exc).__name__,
        }
