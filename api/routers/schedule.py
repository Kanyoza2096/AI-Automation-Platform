from fastapi import APIRouter, Depends, Request, HTTPException
from typing import Optional
from api.dependencies import *
from api.v1_models import *
from api.v1_helpers import *
from api.v1_stores import *

router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════
# SCHEDULE
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/schedule")
async def get_schedule(
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Get scheduler configuration from BrandSchedulerManager.

    Shape contract — the frontend's LiveResourceList expects `schedule` to
    be an ARRAY. Two prior versions broke this contract:

      1. v1 called `manager.get_status()`, which doesn't exist. The
         AttributeError was caught by _safe_err and returned a 500 with a
         generic "Internal server error" — the Scheduler page showed
         "Backend unreachable" with no diagnostic.

      2. v2 (this file's predecessor) returned
             {"schedule": {"health": ..., "brands": [...], "totals": ...}}
         — `schedule` was an OBJECT containing `brands`. The frontend's
         listKeys searched for an array at `response.schedule` and found
         an object. It fell through the remaining listKeys (`slots`,
         `items`, `data`, `results`, `jobs`), none of which exist. The
         page correctly showed "No records from backend" while the
         endpoint was returning two running brand schedulers.

    v3 (this version) returns `schedule` as a flat array of per-brand
    status dicts, matching the frontend's expectation. Health and totals
    move to the top level so callers that need them aren't broken — they
    just have to fetch `response.health` and `response.totals` instead of
    `response.schedule.health` and `response.schedule.totals`.
    """
    try:
        from scheduler.brand_scheduler import BrandSchedulerManager
        manager = await _async_wrap(container.resolve, BrandSchedulerManager)
        return _ok({
            "schedule": manager.get_all_status(),
            "health": manager.health_check(),
            "totals": manager.get_stats(),
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/schedule")


@router.put("/schedule")
async def update_schedule(
    body: ScheduleUpdateRequest,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Update scheduler configuration.

    NOTE: MUTATION CONTRACT UNAVAILABLE — matches the frontend's own badge.
    BrandSchedulerManager exposes start/stop/trigger but not a public
    per-brand schedule mutation method. Until one exists, return 501 rather
    than pretending the update succeeded. The frontend already displays
    MUTATION CONTRACT UNAVAILABLE and disables the controls.
    """
    return _err(
        "Scheduler mutation is not implemented; the frontend already "
        "disables this control (MUTATION CONTRACT UNAVAILABLE).",
        501,
    )
