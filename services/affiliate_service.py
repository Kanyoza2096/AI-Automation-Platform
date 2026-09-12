from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from domain.affiliate.attribution import safe_event_id
from domain.affiliate.compliance import enforce_disclosure
from domain.affiliate.policies import AffiliateDisclosurePolicy, validate_destination


class AffiliateService:
    """Durable affiliate registry, attribution and revenue evidence service."""

    def __init__(self, db=None):
        self.db = db

    def _require_db(self):
        if self.db is None:
            raise RuntimeError("affiliate service requires the authoritative database")
        return self.db

    def create_product(self, *, workspace_id: str, name: str, provider: str, program: str,
                       destination_domains: list[str], commission_rate: float = 0.0,
                       currency: str = "USD", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        if not workspace_id or not name or not provider or not program:
            raise ValueError("workspace_id, name, provider and program are required")
        domains = [str(d).strip().lower().rstrip(".") for d in destination_domains if str(d).strip()]
        if not domains:
            raise ValueError("at least one approved destination domain is required")
        if not 0 <= float(commission_rate) <= 1:
            raise ValueError("commission_rate must be between 0 and 1")
        row = {
            "workspace_id": workspace_id, "name": name.strip(), "provider": provider.strip().lower(),
            "program": program.strip(), "destination_domains": domains,
            "commission_rate": float(commission_rate), "currency": currency.upper(),
            "metadata": metadata or {}, "status": "active",
        }
        result = self._require_db().table("affiliate_products").insert(row).execute()
        data = getattr(result, "data", None) or []
        if not data:
            raise RuntimeError("affiliate product creation failed")
        return data[0]

    def create_link(self, *, workspace_id: str, product_id: str, destination_url: str,
                    slug: str | None = None, campaign: str = "", content_id: str | None = None,
                    metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        db = self._require_db()
        product = db.table("affiliate_products").select("*").eq("id", product_id).eq("workspace_id", workspace_id).maybe_single().execute()
        product_row = getattr(product, "data", None)
        if not product_row:
            raise ValueError("affiliate product not found in workspace")
        validate_destination(destination_url, product_row.get("destination_domains") or [])
        final_slug = (slug or secrets.token_urlsafe(9)).strip().lower()
        if not final_slug or len(final_slug) > 120:
            raise ValueError("invalid affiliate slug")
        row = {
            "workspace_id": workspace_id, "product_id": product_id, "slug": final_slug,
            "destination_url": destination_url, "campaign": campaign, "content_id": content_id,
            "metadata": metadata or {}, "status": "active",
        }
        try:
            result = db.table("affiliate_links").insert(row).execute()
        except Exception as exc:
            raise ValueError("affiliate link slug already exists or is invalid") from exc
        data = getattr(result, "data", None) or []
        if not data:
            raise RuntimeError("affiliate link creation failed")
        return data[0]

    def resolve_redirect(self, slug: str) -> dict[str, Any]:
        db = self._require_db()
        result = db.table("affiliate_links").select("*, affiliate_products!inner(destination_domains,provider,program,status)").eq("slug", slug).eq("status", "active").maybe_single().execute()
        row = getattr(result, "data", None)
        if not row or row.get("affiliate_products", {}).get("status") != "active":
            raise LookupError("affiliate link not found")
        product = row.get("affiliate_products") or {}
        validate_destination(row.get("destination_url"), product.get("destination_domains") or [])
        return row

    def record_click(self, *, link_id: str, workspace_id: str | None = None, event_id: str | None = None,
                     metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        db = self._require_db()
        payload = {"p_link_id": link_id, "p_workspace_id": workspace_id, "p_event_id": event_id,
                   "p_metadata": metadata or {}}
        result = db.rpc("record_affiliate_click", payload).execute()
        data = getattr(result, "data", None)
        return data if isinstance(data, dict) else {"ok": bool(data)}

    def ingest_conversion(self, *, product_id: str, provider: str, external_order_id: str,
                          amount: float, commission: float, currency: str, status: str = "approved",
                          event_id: str | None = None, occurred_at: str | None = None,
                          metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        if amount < 0 or commission < 0:
            raise ValueError("amount and commission cannot be negative")
        if status not in {"pending", "approved", "reversed", "paid"}:
            raise ValueError("invalid conversion status")
        db = self._require_db()
        product = db.table("affiliate_products").select("workspace_id,provider").eq("id", product_id).maybe_single().execute()
        product_row = getattr(product, "data", None)
        if not product_row:
            raise ValueError("affiliate product not found")
        if str(product_row.get("provider")) != str(provider).lower():
            raise ValueError("provider does not match affiliate product")
        eid = event_id or safe_event_id({"external_order_id": external_order_id, "status": status}, provider)
        row = {
            "workspace_id": product_row["workspace_id"], "product_id": product_id, "provider": provider.lower(),
            "external_order_id": external_order_id, "event_id": eid, "amount": float(amount),
            "commission": float(commission), "currency": currency.upper(), "status": status,
            "occurred_at": occurred_at or datetime.now(timezone.utc).isoformat(), "metadata": metadata or {},
        }
        result = db.rpc("upsert_affiliate_conversion", {"p_payload": row}).execute()
        data = getattr(result, "data", None)
        if isinstance(data, dict):
            return data
        return {"ok": bool(data), "event_id": eid}

    def analytics(self, *, workspace_id: str, days: int = 30) -> dict[str, Any]:
        if days < 1 or days > 3650:
            raise ValueError("days must be between 1 and 3650")
        since = datetime.now(timezone.utc) - timedelta(days=days)
        result = self._require_db().table("affiliate_revenue_daily").select("*").eq("workspace_id", workspace_id).gte("day", since.date().isoformat()).execute()
        rows = getattr(result, "data", None) or []
        return {
            "days": days, "since": since.isoformat(), "rows": rows,
            "clicks": sum(int(r.get("clicks") or 0) for r in rows),
            "conversions": sum(int(r.get("conversions") or 0) for r in rows),
            "revenue": sum(float(r.get("revenue") or 0) for r in rows),
            "commission": sum(float(r.get("commission") or 0) for r in rows),
        }

    @staticmethod
    def prepare_promotion(content: str, *, disclosure_required: bool = True,
                           disclosure_text: str | None = None) -> dict[str, Any]:
        policy = AffiliateDisclosurePolicy(required=disclosure_required, disclosure_text=disclosure_text or AffiliateDisclosurePolicy().disclosure_text)
        decision = enforce_disclosure(content, policy)
        return {"content": decision.content, "disclosure_added": decision.disclosure_added, "allowed": decision.allowed}
