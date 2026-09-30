"""
Sprint 5 — SLO breach alerts via webhook.

Env:
  SLO_ALERT_WEBHOOK_URL=https://hooks.slack.com/... or Discord/custom
  SLO_ALERT_COOLDOWN_SECONDS=300
  SLO_ALERT_ENABLED=true
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.request
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("kanyoza.core.slo_alerts")


class SloAlerter:
    def __init__(self) -> None:
        self._last_sent: Dict[str, float] = {}
        self._task: Optional[asyncio.Task] = None
        self._running = False

    @property
    def webhook_url(self) -> Optional[str]:
        return (os.getenv("SLO_ALERT_WEBHOOK_URL") or "").strip() or None

    @property
    def enabled(self) -> bool:
        if os.getenv("SLO_ALERT_ENABLED", "true").lower() in ("0", "false", "no"):
            return False
        return bool(self.webhook_url)

    @property
    def cooldown(self) -> float:
        try:
            return float(os.getenv("SLO_ALERT_COOLDOWN_SECONDS", "300"))
        except ValueError:
            return 300.0

    def evaluate_and_alert(self, slo: Dict[str, Any]) -> List[str]:
        """Sync check; returns breach keys that were alerted."""
        if not self.enabled:
            return []
        breaches = list(slo.get("breaches") or [])
        if not breaches and slo.get("status") == "healthy":
            return []
        if slo.get("status") not in ("degraded", "critical") and not breaches:
            return []

        ws = slo.get("workspace_id") or slo.get("scope") or "platform"
        key = f"{ws}:{','.join(sorted(breaches))}:{slo.get('status')}"
        now = time.monotonic()
        last = self._last_sent.get(key, 0)
        if now - last < self.cooldown:
            return []

        payload = {
            "text": (
                f"[Kanyoza SLO] status={slo.get('status')} workspace={ws} "
                f"breaches={breaches} "
                f"publish_success_rate={((slo.get('publish') or {}).get('success_rate'))} "
                f"outbox_depth={((slo.get('outbox') or {}).get('depth'))} "
                f"outbox_dead={((slo.get('outbox') or {}).get('dead'))} "
                f"bus_dead_letters={((slo.get('bus') or {}).get('dead_letters'))}"
            ),
            "slo": {
                "status": slo.get("status"),
                "workspace_id": ws,
                "breaches": breaches,
                "publish": slo.get("publish"),
                "outbox": slo.get("outbox"),
                "bus": slo.get("bus"),
            },
        }
        if self._post_webhook(payload):
            self._last_sent[key] = now
            return breaches
        return []

    def _post_webhook(self, payload: Dict[str, Any]) -> bool:
        url = self.webhook_url
        if not url:
            return False
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json", "User-Agent": "Kanyoza-SLO-Alerter/1"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                ok = 200 <= getattr(resp, "status", 200) < 300
                logger.info("SLO webhook posted status=%s", getattr(resp, "status", "?"))
                return ok
        except Exception as exc:
            logger.warning("SLO webhook failed: %s", exc)
            return False

    async def start_monitor(self, interval_seconds: float = 60.0) -> None:
        if self._running:
            return
        self._running = True

        async def loop():
            while self._running:
                try:
                    await self._scan()
                except Exception as exc:
                    logger.debug("SLO monitor soft: %s", exc)
                await asyncio.sleep(interval_seconds)

        self._task = asyncio.create_task(loop(), name="slo-alerter")
        logger.info("SLO alerter started (enabled=%s)", self.enabled)

    async def _scan(self) -> None:
        if not self.enabled:
            return
        from core.slo_metrics import compute_platform_slos, compute_workspace_slos
        from core.decision_gate import decision_gate
        from core.publish_outbox import publish_outbox

        workspaces: Set[str] = set()
        for d in decision_gate._decisions.values():
            workspaces.add(d.workspace_id)
        with publish_outbox._lock:
            for i in publish_outbox._mem.values():
                workspaces.add(i.workspace_id)
        if not workspaces:
            workspaces.add("default")

        for ws in workspaces:
            slo = compute_workspace_slos(ws)
            self.evaluate_and_alert(slo)

        # platform rollup for species webhook noise control — only if critical
        plat = compute_platform_slos()
        if plat.get("status") == "critical":
            self.evaluate_and_alert({**plat, "workspace_id": "platform", "breaches": ["platform_critical"]})

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self._task = None


slo_alerter = SloAlerter()
