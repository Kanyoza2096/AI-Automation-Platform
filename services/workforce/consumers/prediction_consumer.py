"""
PredictionAlertConsumer — converts OODA `prediction_alert` events into preemptive tasks.

OODA publishes prediction_alert when its exponential smoothing predictor
extrapolates a future failure. Without this consumer, the prediction is
logged but not acted on. With it, the operator gets a "heads up" task before
the actual failure occurs.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.unified_bus import UnifiedEventBus

logger = logging.getLogger("kanyoza.workforce.consumers.prediction")


class PredictionAlertConsumer:
    def __init__(self, bus: UnifiedEventBus, supabase_client: Any = None):
        self._bus = bus
        self._sb = supabase_client
        self._subscribed = False
        self._events_handled = 0
        self._events_failed = 0

    async def start(self) -> None:
        if self._subscribed:
            return
        try:
            await self._bus.subscribe("prediction_alert", self._on_prediction)
            self._subscribed = True
            logger.info("PredictionAlertConsumer started")
        except Exception as exc:
            logger.error("PredictionAlertConsumer subscribe failed: %s", exc)

    async def _on_prediction(self, event: Any = None, payload: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        try:
            data = payload or (event if isinstance(event, dict) else {})
            if not data:
                return

            service = str(data.get("service", "unknown")).strip()
            issue = str(data.get("predicted_issue", "")).strip()
            probability = float(data.get("probability", 0.0))
            eta_minutes = float(data.get("estimated_time_minutes", 0.0))
            recommended = str(data.get("recommended_action", "")).strip()
            workspace_id = str(data.get("workspace_id", "platform")).strip()

            # Only create tasks for high-confidence predictions
            if probability < 0.7:
                return

            task = {
                "id": f"task_{uuid.uuid4().hex[:12]}",
                "kind": "prediction_warning",
                "title": f"Predicted failure: {service} in ~{eta_minutes:.0f}m",
                "description": issue or f"Predictive model forecasts {service} failure with {probability:.0%} confidence",
                "severity": "medium" if probability < 0.85 else "high",
                "status": "pending",
                "workspace_id": workspace_id,
                "recommended_action": recommended,
                "prediction": {
                    "service": service,
                    "probability": probability,
                    "eta_minutes": eta_minutes,
                },
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

            if self._sb is not None:
                try:
                    await asyncio.to_thread(
                        lambda: self._sb.table("operator_tasks").insert(task).execute()
                    )
                except Exception as exc:
                    logger.warning("Prediction persist failed: %s", exc)

            self._events_handled += 1
            logger.info(
                "prediction_alert created task: service=%s eta=%.0fm prob=%.0f%%",
                service, eta_minutes, probability * 100,
            )
        except Exception as exc:
            self._events_failed += 1
            logger.exception("prediction handling failed: %s", exc)

    def get_status(self) -> Dict[str, Any]:
        return {
            "consumer": "prediction",
            "subscribed": self._subscribed,
            "events_handled": self._events_handled,
            "events_failed": self._events_failed,
        }
