"""Backend → KCPG realtime event bridge.

Publishes authoritative UnifiedEventBus events to the gateway's authenticated
internal event ingress. Browser clients never connect directly to this service.

v1.1 (2026-10-03) — HEALTH PROBE + SIGNATURE ALIGNMENT:
  - health_check renamed to check_health_async and returns a HealthReport.
    The lifecycle ServiceHealthRegistry prefers check_health_async over
    health_check/check_health; naming an async method health_check caused
    the registry to route it through _run_in_thread, which passes the
    coroutine function to loop.run_in_executor and raises
    "coroutines cannot be used with run_in_executor()". The result was
    that kcpg_event_bridge always reported status=unknown.
  - _on_event now normalizes whole-number floats in the body before
    json.dumps. The HMAC digest is computed over the normalized form
    (via _canonical → _normalize_numbers), but the HTTP payload was
    being sent un-normalized. Python serializes 1.0 as "1.0"; JavaScript
    JSON.stringify produces "1". When the gateway recomputed the digest
    from the parsed JSON, the two byte streams differed and signature
    verification failed on any event whose payload contained a whole-
    number float. This was latent — the bridge only sends when
    KCPG_INTERNAL_EVENTS_URL is configured.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import httpx

logger = logging.getLogger("kanyoza.services.kcpg_event_bridge")


class KCPGEventBridge:
    service_name = "kcpg_event_bridge"

    def __init__(self, event_bus):
        self.event_bus = event_bus
        self.url = ""
        self.token = ""
        self.signing_secret = ""
        self._unsubscribe = None
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> bool:
        from config.settings import settings
        self.url = (getattr(settings, "kcpg_internal_events_url", "") or "").strip().rstrip("/")
        self.token = (getattr(settings, "python_service_token", "") or "").strip()
        self.signing_secret = (getattr(settings, "kcpg_event_signing_secret", "") or "").strip()
        if not self.url:
            logger.info("KCPG event bridge disabled: KCPG_INTERNAL_EVENTS_URL not configured")
            return False
        if not self.token or not self.signing_secret:
            raise RuntimeError("KCPG event bridge requires PYTHON_SERVICE_TOKEN and KCPG_EVENT_SIGNING_SECRET")
        self._client = httpx.AsyncClient(timeout=8.0)
        self._unsubscribe = await self.event_bus.subscribe_all(self._on_event)
        logger.info("KCPG event bridge started: %s", self.url)
        return True

    async def stop(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        if self._client:
            await self._client.aclose()
            self._client = None

    async def check_health_async(self):
        """Health probe for the ServiceHealthRegistry.

        The registry prefers check_health_async and expects a HealthReport
        (or a dict, which is normalized). Returning a HealthReport directly
        preserves latency, metadata, and last_success fields.

        States:
          - HEALTHY: bridge started successfully (url set, subscribed)
          - UNKNOWN: bridge disabled (url not configured) or not yet started
          - UNHEALTHY: bridge was configured but subscription was lost

        The bridge reports UNKNOWN when disabled rather than UNHEALTHY:
        a disabled optional bridge is a valid configuration, not a fault.
        """
        from lifecycle import HealthReport, HealthStatus

        configured = bool(self.url)
        subscribed = self._unsubscribe is not None

        if not configured:
            return HealthReport(
                status=HealthStatus.UNKNOWN,
                service_name=self.service_name,
                metadata={
                    "configured": False,
                    "subscribed": False,
                    "note": "KCPG_INTERNAL_EVENTS_URL not configured",
                },
            )

        if configured and subscribed:
            return HealthReport(
                status=HealthStatus.HEALTHY,
                service_name=self.service_name,
                metadata={
                    "configured": True,
                    "subscribed": True,
                    "url_host": self.url.split("://", 1)[-1].split("/", 1)[0],
                },
            )

        # Configured but no subscription — the bridge tried to start and
        # either failed mid-way or was stopped without being disabled.
        # That is a real fault; surface it as unhealthy.
        return HealthReport(
            status=HealthStatus.UNHEALTHY,
            service_name=self.service_name,
            error_message="Bridge configured but not subscribed",
            metadata={"configured": True, "subscribed": False},
        )

    async def _on_event(self, event_data: dict[str, Any]) -> None:
        if not self._client or not self.url:
            return

        payload = event_data.get("payload") if isinstance(event_data.get("payload"), dict) else {}
        # UnifiedEventBus carries trusted scope on the event envelope. Most
        # tenant events also mirror it into payload, but platform-scoped
        # Director requests intentionally keep their payload tenant-neutral.
        # Prefer the explicit payload value for backwards compatibility, then
        # fall back to the trusted envelope field rather than dropping the
        # platform event before it reaches KCPG.
        workspace_id = (
            payload.get("workspace_id")
            or payload.get("workspaceId")
            or event_data.get("workspace_id")
            or event_data.get("workspaceId")
        )
        if not workspace_id:
            logger.debug(
                "KCPG event bridge skipped event without trusted workspace scope event=%s",
                event_data.get("event_name") or event_data.get("event_type"),
            )
            return

        event_id = event_data.get("event_id") or payload.get("event_id")
        event_name = event_data.get("event_name") or payload.get("event_name") or payload.get("type")
        correlation_id = payload.get("correlation_id") or payload.get("correlationId")
        issued_at = datetime.now(timezone.utc).isoformat()

        body = {
            **payload,
            "event_id": str(event_id),
            "issued_at": issued_at,
            "workspace_id": str(workspace_id),
            "correlation_id": str(correlation_id or event_id),
            "type": str(event_name),
            "source": "python-core",
        }

        # Convert all values to JSON-safe primitives.
        body = self._json_safe(body)

        # Normalize whole-number floats (1.0 -> 1) so the bytes sent over
        # the wire match what _canonical hashes. JavaScript's JSON.stringify
        # produces "1" for 1.0; Python's json.dumps produces "1.0". The
        # gateway recomputes the digest from the parsed JSON on its side,
        # so any mismatch breaks the HMAC. This must run before both the
        # digest computation AND the body serialization.
        body = self._normalize_numbers(body)

        canonical = self._canonical(body)
        signature = hmac.new(self.signing_secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()

        # Send the JSON body with the same encoding used for the digest
        # (ensure_ascii=False, compact separators). httpx's `json=` helper
        # uses Python's default JSON encoder, which escapes non-ASCII as
        # \uXXXX and would produce a body whose bytes do not match what
        # the gateway recomputes from the parsed JSON.
        content = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        try:
            response = await self._client.post(
                self.url,
                content=content,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.token}",
                    "X-KCPG-Event-Signature": signature,
                },
            )
            if response.status_code >= 300:
                logger.warning(
                    "KCPG event ingress rejected event=%s status=%s body_keys=%s body=%s",
                    event_name,
                    response.status_code,
                    list(body.keys())[:10],
                    response.text[:300],
                )
        except Exception as exc:
            logger.warning("KCPG event ingress failed event=%s: %s", event_name, str(exc)[:180])

    @staticmethod
    def _json_safe(value: Any) -> Any:
        """Convert values to JSON-safe primitives matching JavaScript JSON.stringify."""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, dict):
            return {str(k): KCPGEventBridge._json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [KCPGEventBridge._json_safe(v) for v in value]
        # Fallback: convert to string
        try:
            json.dumps(value)
            return value
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _normalize_numbers(value: Any) -> Any:
        """Normalize whole-number floats to integers.

        JavaScript JSON.stringify(1.0) -> "1"
        Python json.dumps(1.0)       -> "1.0"

        This mismatch causes invalid HMAC signatures. Called before both
        digest computation and body serialization so the two sides agree.
        """
        if isinstance(value, float):
            if value.is_integer():
                return int(value)
            return value
        if isinstance(value, dict):
            return {
                k: KCPGEventBridge._normalize_numbers(v)
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [KCPGEventBridge._normalize_numbers(v) for v in value]
        return value

    @staticmethod
    def _canonical(body: dict[str, Any]) -> str:
        event_id = body.get("event_id", "")
        issued_at = body.get("issued_at", "")
        workspace_id = body.get("workspace_id", "")
        correlation_id = body.get("correlation_id", "")
        command_id = body.get("command_id", "")
        event_type = body.get("type", "")
        # Body is already normalized by _on_event before calling _canonical.
        # _normalize_numbers is idempotent, so calling it again is safe.
        normalized_body = KCPGEventBridge._normalize_numbers(body)
        # ensure_ascii=False matches JavaScript JSON.stringify, which does
        # not escape non-ASCII characters. The gateway recomputes the digest
        # from the parsed JSON using JSON.stringify, so any \uXXXX escaping
        # here would produce a different SHA-256 and break the HMAC.
        digest = hashlib.sha256(
            json.dumps(
                KCPGEventBridge._canonicalize(normalized_body),
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        return "|".join([
            str(event_id),
            str(issued_at),
            str(workspace_id),
            str(correlation_id),
            str(command_id),
            str(event_type),
            digest,
        ])

    @staticmethod
    def _canonicalize(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: KCPGEventBridge._canonicalize(value[k]) for k in sorted(value)}
        if isinstance(value, list):
            return [KCPGEventBridge._canonicalize(v) for v in value]
        return value
