"""Render cloud deployment adapter for governed infrastructure operations.

Read operations are enabled by default. Deploy/cancel operations require
explicit mutation enablement and call-site confirmation. A requested deploy is
not considered healthy merely because Render accepted the request.
"""
from __future__ import annotations

import os
from typing import Any, Optional
import httpx
from services.connectors.enterprise_provider_base import EnterpriseProviderAdapter, ProviderAdapterError


class RenderAdapter(EnterpriseProviderAdapter):
    provider = "render"
    API_BASE = "https://api.render.com/v1"

    def __init__(self, *, token: Optional[str] = None, base_url: str = API_BASE,
                 client: Optional[httpx.Client] = None, timeout: float = 20.0,
                 allow_mutations: bool = False, allow_high_risk: bool = False):
        super().__init__(token=token or os.getenv("RENDER_API_KEY", ""), base_url=base_url,
                         client=client, timeout=timeout, allow_mutations=allow_mutations,
                         allow_high_risk=allow_high_risk)

    def health(self) -> dict[str, Any]:
        code, data, _ = self._request("GET", "services", params={"limit": 1})
        return {"ok": code == 200, "provider": self.provider,
                "sample_count": len(data) if isinstance(data, list) else 0}

    def list_services(self, *, limit: int = 20, cursor: Optional[str] = None,
                      name: Optional[str] = None, service_type: Optional[str] = None) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100: raise ValueError("limit must be 1..100")
        params: dict[str, Any] = {"limit": limit}
        if cursor: params["cursor"] = cursor
        if name: params["name"] = name
        if service_type: params["type"] = service_type
        _, data, _ = self._request("GET", "services", params=params)
        return data or []

    def get_service(self, service_id: str) -> dict[str, Any]:
        if not service_id: raise ValueError("service_id is required")
        _, data, _ = self._request("GET", f"services/{service_id}")
        return data

    def list_deploys(self, service_id: str, *, limit: int = 20, cursor: Optional[str] = None) -> list[dict[str, Any]]:
        if not service_id: raise ValueError("service_id is required")
        if not 1 <= limit <= 100: raise ValueError("limit must be 1..100")
        params: dict[str, Any] = {"limit": limit}
        if cursor: params["cursor"] = cursor
        _, data, _ = self._request("GET", f"services/{service_id}/deploys", params=params)
        return data or []

    def get_deploy(self, deploy_id: str) -> dict[str, Any]:
        if not deploy_id: raise ValueError("deploy_id is required")
        _, data, _ = self._request("GET", f"deploys/{deploy_id}")
        return data

    def trigger_deploy(self, service_id: str, *, commit_id: Optional[str] = None,
                       clear_cache: bool = False, confirm: bool = False,
                       production: bool = False, confirm_production: bool = False) -> dict[str, Any]:
        self._require_mutation(high_risk=True, confirm=confirm,
                               production_confirmation=production, confirm_production=confirm_production)
        if not service_id: raise ValueError("service_id is required")
        payload: dict[str, Any] = {"clearCache": "clear" if clear_cache else "do_not_clear"}
        if commit_id: payload["commitId"] = commit_id
        try:
            _, data, headers = self._request("POST", f"services/{service_id}/deploys", json=payload, mutation=True)
            deploy = data.get("deploy", data) if isinstance(data, dict) else data
            deploy_id = deploy.get("id") if isinstance(deploy, dict) else None
            if deploy_id:
                # The deploy resource ID proves request acceptance, not successful rollout.
                return self._receipt(status="uncertain", object_id=deploy_id,
                    request_id=headers.get("X-Request-Id"),
                    error="Render deploy was created; poll deploy state before recording terminal success",
                    metadata={"service_id": service_id, "commit_id": commit_id,
                              "deploy_status": deploy.get("status"), "reconciliation_required": True})
            return self._receipt(status="uncertain", request_id=headers.get("X-Request-Id"),
                error="Render response lacked a deploy ID; reconcile service deploy history",
                metadata={"service_id": service_id, "commit_id": commit_id, "reconciliation_required": True})
        except ProviderAdapterError as exc: return self._handle_mutation_error(exc)

    def cancel_deploy(self, deploy_id: str, *, confirm: bool = False) -> dict[str, Any]:
        self._require_mutation(high_risk=True, confirm=confirm)
        if not deploy_id: raise ValueError("deploy_id is required")
        try:
            code, data, headers = self._request("POST", f"deploys/{deploy_id}/cancel", mutation=True)
            returned_id = data.get("id") if isinstance(data, dict) else None
            return self._receipt(status="uncertain", object_id=returned_id or deploy_id,
                request_id=headers.get("X-Request-Id"),
                error="Cancellation requested; poll deploy state before recording terminal success",
                metadata={"http_status": code, "deploy_id": deploy_id, "reconciliation_required": True})
        except ProviderAdapterError as exc: return self._handle_mutation_error(exc)

    def reconcile_deploy(self, service_id: str, deploy_id: str) -> dict[str, Any]:
        """Read authoritative current deploy state; never trigger another deploy."""
        deploy = self.get_deploy(deploy_id)
        inner = deploy.get("deploy", deploy) if isinstance(deploy, dict) else {}
        observed_service = inner.get("serviceId") or inner.get("service_id")
        if observed_service and str(observed_service) != str(service_id):
            raise ValueError("deploy does not belong to the requested service")
        state = str(inner.get("status") or "unknown").lower()
        if state in {"live", "succeeded", "success"}:
            status = "succeeded"
        elif state in {"failed", "build_failed", "update_failed", "pre_deploy_failed", "canceled", "cancelled", "deactivated"}:
            status = "failed"
        else:
            status = "in_progress"
        return {"provider": self.provider, "status": status, "deploy_id": deploy_id,
                "service_id": service_id, "provider_object_id": deploy_id if status == "succeeded" else None,
                "observed_object_id": deploy_id, "raw_status": state, "deploy": inner}
