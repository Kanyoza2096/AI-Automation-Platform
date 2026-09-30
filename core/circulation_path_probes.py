"""
End-to-end circulation path probes (synthetic / dry-run).

Proves data can move through critical paths without live side effects:
  1. DB read path (workspaces / brands)
  2. Content generation dry path (optional, timeout-bound)
  3. Publish pipeline dry_run
  4. Facebook connector health (token validate, no post)

Results feed DFMO workflow_score — separate from infrastructure probes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("kanyoza.circulation.paths")


@dataclass
class PathProbeResult:
    name: str
    ok: bool
    duration_ms: float = 0.0
    detail: str = ""
    critical: bool = False  # if True, failure strongly affects workflow score


async def probe_db_read(supabase=None) -> PathProbeResult:
    t0 = time.monotonic()
    try:
        if supabase is None:
            from services.supabase import get_service_client_from_settings
            supabase = get_service_client_from_settings()
        if supabase is None:
            return PathProbeResult("db_read", False, detail="no supabase client", critical=True)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: supabase.table("workspaces").select("id").limit(1).execute(),
        )
        return PathProbeResult(
            "db_read",
            True,
            duration_ms=round((time.monotonic() - t0) * 1000, 1),
            detail="workspaces select ok",
            critical=True,
        )
    except Exception as exc:
        return PathProbeResult(
            "db_read",
            False,
            duration_ms=round((time.monotonic() - t0) * 1000, 1),
            detail=str(exc)[:160],
            critical=True,
        )


async def probe_content_dry(timeout: float = 12.0) -> PathProbeResult:
    """Lightweight content path: ensure ContentEngine is importable/callable."""
    t0 = time.monotonic()
    try:
        from services.content_engine import content_engine

        async def _run():
            # Prefer a cheap health/status if available
            if hasattr(content_engine, "health_check"):
                hc = content_engine.health_check()
                if asyncio.iscoroutine(hc):
                    hc = await hc
                return hc
            if hasattr(content_engine, "get_status"):
                st = content_engine.get_status()
                if asyncio.iscoroutine(st):
                    st = await st
                return st
            return {"ok": True, "note": "content_engine present"}

        result = await asyncio.wait_for(_run(), timeout=timeout)
        ok = True
        if isinstance(result, dict) and result.get("ok") is False:
            ok = False
        return PathProbeResult(
            "content_path",
            ok,
            duration_ms=round((time.monotonic() - t0) * 1000, 1),
            detail=str(result)[:120] if result is not None else "ok",
            critical=False,
        )
    except asyncio.TimeoutError:
        return PathProbeResult(
            "content_path",
            False,
            duration_ms=round((time.monotonic() - t0) * 1000, 1),
            detail="timeout",
            critical=False,
        )
    except Exception as exc:
        return PathProbeResult(
            "content_path",
            False,
            duration_ms=round((time.monotonic() - t0) * 1000, 1),
            detail=str(exc)[:160],
            critical=False,
        )


async def probe_publish_dry_run() -> PathProbeResult:
    """Canonical publish pipeline in dry_run mode — no outbox / no Graph post."""
    t0 = time.monotonic()
    try:
        from domain.publishing.canonical_pipeline import run_canonical_publish

        result = await asyncio.wait_for(
            run_canonical_publish(
                workspace_id="platform",
                brand_id="dfmo_probe",
                topic="DFMO path probe",
                caption="DFMO circulation path probe (dry run)",
                platform="facebook",
                dry_run=True,
                durable=False,
                source="dfmo",
            ),
            timeout=15.0,
        )
        # Accept various success shapes
        ok = False
        detail = ""
        if isinstance(result, dict):
            status = str(result.get("status") or result.get("outcome") or "").lower()
            ok = status in ("ok", "success", "dry_run", "allow", "allowed") or result.get("dry_run") is True
            if result.get("ok") is True:
                ok = True
            detail = status or str(list(result.keys())[:6])
        else:
            ok = result is not None
            detail = type(result).__name__
        return PathProbeResult(
            "publish_dry_run",
            ok,
            duration_ms=round((time.monotonic() - t0) * 1000, 1),
            detail=detail[:160],
            critical=True,
        )
    except asyncio.TimeoutError:
        return PathProbeResult(
            "publish_dry_run",
            False,
            duration_ms=round((time.monotonic() - t0) * 1000, 1),
            detail="timeout",
            critical=True,
        )
    except Exception as exc:
        # Pipeline may require more context — soft fail unless import missing
        msg = str(exc)[:160]
        critical = "No module" in msg or "cannot import" in msg.lower()
        return PathProbeResult(
            "publish_dry_run",
            False,
            duration_ms=round((time.monotonic() - t0) * 1000, 1),
            detail=msg,
            critical=critical,
        )


async def probe_facebook_health() -> PathProbeResult:
    """Validate connector health without posting."""
    t0 = time.monotonic()
    try:
        from services.facebook import facebook as fb
        if hasattr(fb, "health_check"):
            hc = fb.health_check()
            if asyncio.iscoroutine(hc):
                hc = await hc
            ok = False
            if isinstance(hc, dict):
                st = str(hc.get("status", "")).lower()
                ok = st in ("healthy", "ok", "nominal") or hc.get("ok") is True
                # disabled is not a flow blockage for all deployments
                if st == "disabled":
                    return PathProbeResult(
                        "facebook_connector",
                        True,
                        duration_ms=round((time.monotonic() - t0) * 1000, 1),
                        detail="disabled (skipped)",
                        critical=False,
                    )
            return PathProbeResult(
                "facebook_connector",
                ok,
                duration_ms=round((time.monotonic() - t0) * 1000, 1),
                detail=str(hc)[:120] if hc else "empty",
                critical=False,
            )
        if hasattr(fb, "is_configured") and not fb.is_configured:
            return PathProbeResult(
                "facebook_connector",
                True,
                duration_ms=round((time.monotonic() - t0) * 1000, 1),
                detail="not configured",
                critical=False,
            )
        return PathProbeResult(
            "facebook_connector",
            True,
            duration_ms=round((time.monotonic() - t0) * 1000, 1),
            detail="present",
            critical=False,
        )
    except Exception as exc:
        return PathProbeResult(
            "facebook_connector",
            False,
            duration_ms=round((time.monotonic() - t0) * 1000, 1),
            detail=str(exc)[:160],
            critical=False,
        )


async def run_all_path_probes(supabase=None) -> List[PathProbeResult]:
    """Run probes concurrently (bounded)."""
    results = await asyncio.gather(
        probe_db_read(supabase),
        probe_publish_dry_run(),
        probe_facebook_health(),
        probe_content_dry(),
        return_exceptions=True,
    )
    out: List[PathProbeResult] = []
    for r in results:
        if isinstance(r, PathProbeResult):
            out.append(r)
        elif isinstance(r, Exception):
            out.append(PathProbeResult("path_probe", False, detail=str(r)[:160], critical=False))
    return out


def score_path_results(results: List[PathProbeResult]) -> float:
    """0-100 workflow path score."""
    if not results:
        return 50.0
    weights = []
    scores = []
    for r in results:
        w = 2.0 if r.critical else 1.0
        weights.append(w)
        scores.append(100.0 if r.ok else 0.0)
    total_w = sum(weights) or 1.0
    return round(sum(s * w for s, w in zip(scores, weights)) / total_w, 1)
