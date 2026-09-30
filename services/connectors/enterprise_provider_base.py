"""Shared, sector-neutral HTTP boundary for enterprise provider adapters.

Mutation requests are never automatically retried. A transport failure after a
write is ambiguous and must be reconciled instead of blindly replayed.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional
from urllib.parse import urlparse
import httpx


class ProviderAdapterError(RuntimeError):
    def __init__(self, provider: str, message: str, *, status_code: Optional[int] = None,
                 request_id: Optional[str] = None, ambiguous: bool = False):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.request_id = request_id
        self.ambiguous = ambiguous


class EnterpriseProviderAdapter:
    provider = "unknown"
    DEFAULT_TIMEOUT = 20.0

    def __init__(self, *, token: str, base_url: str, client: Optional[httpx.Client] = None,
                 timeout: float = DEFAULT_TIMEOUT, allow_mutations: bool = False,
                 allow_high_risk: bool = False):
        if not token or not token.strip():
            raise ValueError(f"{self.provider} token is required")
        parsed = urlparse(base_url)
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "testserver"}:
            raise ValueError("provider API base URL must use HTTPS")
        self._token = token.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.allow_mutations = allow_mutations
        self.allow_high_risk = allow_high_risk
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json",
                "User-Agent": "KDO-Connector-Fabric/1.0"}

    def _require_mutation(self, *, high_risk: bool = False, confirm: bool = False,
                          production_confirmation: bool = False, confirm_production: bool = False) -> None:
        if not self.allow_mutations:
            raise PermissionError(f"{self.provider} mutations are disabled; enable them explicitly")
        if high_risk and not self.allow_high_risk:
            raise PermissionError(f"{self.provider} high-risk operations are disabled")
        if high_risk and not confirm:
            raise PermissionError("high-risk operation requires explicit confirm=True")
        if production_confirmation and not confirm_production:
            raise PermissionError("production operation requires explicit confirm_production=True")

    def _request(self, method: str, path: str, *, params: Optional[Mapping[str, Any]] = None,
                 json: Any = None, data: Any = None, headers: Optional[Mapping[str, str]] = None,
                 mutation: bool = False) -> tuple[int, Any, Mapping[str, str]]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        request_headers = self._headers()
        if headers:
            request_headers.update(headers)
        try:
            response = self.client.request(method, url, params=params, json=json, data=data,
                                           headers=request_headers, timeout=self.timeout)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProviderAdapterError(self.provider,
                f"transport failure during {'mutation' if mutation else 'read'}; reconcile before retrying",
                ambiguous=mutation) from exc
        request_id = response.headers.get("X-GitHub-Request-Id") or response.headers.get("X-Request-Id")
        if response.status_code >= 400:
            # A 5xx after a write can mean the provider committed before failing.
            ambiguous = mutation and response.status_code >= 500
            message = f"{self.provider} API returned HTTP {response.status_code}"
            try:
                payload = response.json()
                detail = payload.get("message") if isinstance(payload, dict) else None
                if detail:
                    message += f": {str(detail)[:300]}"
            except (ValueError, TypeError):
                pass
            raise ProviderAdapterError(self.provider, message, status_code=response.status_code,
                                       request_id=request_id, ambiguous=ambiguous)
        if response.status_code == 204 or not response.content:
            payload = None
        else:
            try:
                payload = response.json()
            except ValueError:
                payload = {"text": response.text[:1000]}
        return response.status_code, payload, response.headers

    def _receipt(self, *, status: str, object_id: Optional[Any] = None,
                 request_id: Optional[str] = None, error: Optional[str] = None,
                 metadata: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Return fields understood by KDO's canonical ProviderReceipt protocol."""
        result = {"ok": status in {"succeeded", "in_progress"}, "provider": self.provider,
                  "platform": self.provider, "status": status, "outcome": status,
                  "provider_request_id": request_id, "request_id": request_id,
                  "provider_object_id": str(object_id) if object_id is not None and status == "succeeded" else None,
                  "observed_object_id": str(object_id) if object_id is not None and status != "succeeded" else None,
                  "id": str(object_id) if object_id is not None else None,
                  "error": error, "metadata": metadata or {}}
        return result

    def _handle_mutation_error(self, exc: ProviderAdapterError) -> dict[str, Any]:
        return self._receipt(status="uncertain" if exc.ambiguous else "failed",
                             request_id=exc.request_id, error=str(exc),
                             metadata={"http_status": exc.status_code, "reconciliation_required": exc.ambiguous})
