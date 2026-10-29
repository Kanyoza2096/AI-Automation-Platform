"""Governed Action Kernel contract for brand posting-schedule updates.

The action writes one brand's schedule inside the requested workspace. It is
registered only by the Director's governed handoff, which requires a durable
Action Kernel database and an explicitly approved Director proposal.
"""
from __future__ import annotations

import inspect
from typing import Any

from services.action_kernel import ActionContract, ActionIntent

BRAND_UPDATE_POSTING_SCHEDULE = "brand_update_posting_schedule"


async def _update_schedule(parameters: dict[str, Any], intent: ActionIntent) -> dict[str, Any]:
    brand_id = str(parameters.get("brand_id") or "").strip()
    workspace_id = intent.workspace_id
    raw_hours = parameters.get("posting_hours")
    if not brand_id:
        return {"ok": False, "outcome": "failed", "error": "brand_id required"}
    if not isinstance(raw_hours, list) or not raw_hours:
        return {"ok": False, "outcome": "failed", "error": "posting_hours must be a non-empty list"}
    try:
        hours = [int(hour) for hour in raw_hours]
    except (TypeError, ValueError):
        return {"ok": False, "outcome": "failed", "error": "posting_hours must be integers"}
    if any(hour < 0 or hour > 23 for hour in hours) or len(hours) > 24 or len(set(hours)) != len(hours):
        return {"ok": False, "outcome": "failed", "error": "posting_hours must be unique integers in 0..23"}

    from repositories.brand_repository import BrandRepository
    repo = parameters.get("_brand_repository") or BrandRepository()
    brand = repo.find_by_id(brand_id)
    if brand is None:
        return {"ok": False, "outcome": "failed", "error": "brand_not_found"}
    if str(getattr(brand, "workspace_id", "")) != workspace_id:
        return {"ok": False, "outcome": "failed", "error": "brand_workspace_mismatch"}

    schedule = dict(getattr(brand, "posting_schedule", None) or {})
    previous_hours = list(schedule.get("content_hours") or [])
    schedule["content_hours"] = sorted(hours)
    brand.posting_schedule = schedule
    saved = repo.save(brand)
    if inspect.isawaitable(saved):
        await saved
    return {
        "ok": True,
        "outcome": "succeeded",
        "object_id": brand_id,
        "internal_receipt": {
            "kind": "KDOInternalDomainReceipt",
            "action_type": BRAND_UPDATE_POSTING_SCHEDULE,
            "workspace_id": workspace_id,
            "object_id": brand_id,
            "previous_hours": previous_hours,
            "posting_hours": sorted(hours),
        },
        "data": {"brand_id": brand_id, "workspace_id": workspace_id, "posting_hours": sorted(hours)},
    }


def register_brand_schedule_action(kernel, *, brand_repository=None) -> None:
    async def handler(parameters: dict[str, Any], intent: ActionIntent) -> dict[str, Any]:
        payload = dict(parameters or {})
        if brand_repository is not None:
            payload["_brand_repository"] = brand_repository
        return await _update_schedule(payload, intent)

    kernel.register(ActionContract(
        action_type=BRAND_UPDATE_POSTING_SCHEDULE,
        risk_level="medium",
        required_parameters=("brand_id", "posting_hours"),
        requires_approval=True,
        irreversible=False,
        inline_with_decision=True,
        autonomous_retryable=False,
        handler=handler,
    ))
