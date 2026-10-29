from typing import Any, Dict, List, Optional
import logging

from pydantic import ValidationError

from repositories.base import Repository
from models.brand import Brand

logger = logging.getLogger("kanyoza.repositories.brand")


class BrandRepository(Repository[Brand]):
    """Supabase + In-Memory fallback repository for Brands.

    Hydration is per-row: a single malformed row does not fail the entire
    query. Failed rows are logged with their identifier and skipped; the
    rest of the result set is returned. This prevents one bad record from
    silently emptying the brand list — which had previously caused the
    scheduler to skip content generation for every brand.
    """

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._in_memory: Dict[str, Brand] = {}

    # ── Hydration helper ────────────────────────────────────────────────

    @staticmethod
    def _hydrate_row(row: Dict[str, Any]) -> Optional[Brand]:
        """Hydrate one row into a Brand. Returns None on validation failure.

        Never raises. Logs a structured warning that includes the row's
        identifier so the failing brand is discoverable from logs alone.
        """
        try:
            return Brand(**row)
        except ValidationError as exc:
            logger.warning(
                "Brand row failed validation: id=%s slug=%s errors=%s",
                row.get("id"),
                row.get("slug"),
                exc.errors(include_url=False),
            )
            return None
        except Exception as exc:
            logger.warning(
                "Brand row hydration raised: id=%s slug=%s error=%s",
                row.get("id"),
                row.get("slug"),
                str(exc)[:300],
            )
            return None

    def _hydrate_rows(
        self,
        rows: List[Dict[str, Any]],
        *,
        context: str,
    ) -> List[Brand]:
        """Hydrate a list of rows with per-row error isolation.

        Logs an aggregate ERROR only if every row failed AND rows were
        present — that is the signal that something systemic is wrong
        (schema drift, migration mismatch). Individual row failures are
        warnings.
        """
        brands: List[Brand] = []
        failed_ids: List[str] = []
        for row in rows or []:
            brand = self._hydrate_row(row)
            if brand is None:
                failed_ids.append(
                    str(row.get("id") or row.get("slug") or "<unknown>")
                )
            else:
                brands.append(brand)

        if failed_ids and not brands:
            logger.error(
                "Brand hydration fully failed: context=%s failures=%d "
                "failed_ids=%s — check schema migration",
                context,
                len(failed_ids),
                failed_ids,
            )
        elif failed_ids:
            logger.error(
                "Brand hydration partial failure: context=%s "
                "hydrated=%d failed=%d failed_ids=%s",
                context,
                len(brands),
                len(failed_ids),
                failed_ids,
            )
        return brands

    # ── Repository interface ────────────────────────────────────────────

    def find_by_id(self, record_id: str) -> Optional[Brand]:
        if not self.supabase:
            return self._in_memory.get(record_id)
        try:
            res = self.supabase.table("brands").select("*").eq("id", record_id).execute()
            if not res.data:
                return None
            return self._hydrate_row(res.data[0])
        except Exception as e:
            logger.error(
                "Failed to find brand: id=%s error_type=%s error=%s",
                record_id,
                type(e).__name__,
                str(e)[:300],
            )
            return self._in_memory.get(record_id)

    def save(self, entity: Brand) -> Brand:
        if not self.supabase:
            self._in_memory[entity.id] = entity
            return entity
        try:
            data = entity.model_dump(mode="json")
            res = self.supabase.table("brands").upsert(data).execute()
            if res.data:
                hydrated = self._hydrate_row(res.data[0])
                if hydrated is not None:
                    return hydrated
                logger.warning(
                    "Save succeeded but returned row failed hydration: id=%s",
                    entity.id,
                )
            return entity
        except Exception as e:
            logger.error(
                "Failed to save brand: id=%s error_type=%s error=%s",
                entity.id,
                type(e).__name__,
                str(e)[:300],
            )
            self._in_memory[entity.id] = entity
            return entity

    def delete(self, record_id: str) -> bool:
        if not self.supabase:
            if record_id in self._in_memory:
                del self._in_memory[record_id]
                return True
            return False
        try:
            self.supabase.table("brands").delete().eq("id", record_id).execute()
            return True
        except Exception as e:
            logger.error(
                "Failed to delete brand: id=%s error_type=%s error=%s",
                record_id,
                type(e).__name__,
                str(e)[:300],
            )
            if record_id in self._in_memory:
                del self._in_memory[record_id]
                return True
            return False

    def find_by_workspace(self, workspace_id: str) -> List[Brand]:
        if not self.supabase:
            return [
                b for b in self._in_memory.values()
                if b.workspace_id == workspace_id
            ]
        try:
            res = self.supabase.table("brands").select("*").eq(
                "workspace_id", workspace_id
            ).execute()
            return self._hydrate_rows(
                res.data or [],
                context=f"find_by_workspace:{workspace_id}",
            )
        except Exception as e:
            logger.error(
                "Failed to query brands by workspace: workspace=%s "
                "error_type=%s error=%s",
                workspace_id,
                type(e).__name__,
                str(e)[:300],
            )
            return [
                b for b in self._in_memory.values()
                if b.workspace_id == workspace_id
            ]

    def find_all(self) -> List[Brand]:
        """Return all brands regardless of workspace.

        Per-row hydration: a malformed row is skipped with a WARN that
        includes its id. The whole call only returns the in-memory
        fallback when the *query itself* fails.
        """
        if not self.supabase:
            return list(self._in_memory.values())
        try:
            res = self.supabase.table("brands").select("*").execute()
            return self._hydrate_rows(
                res.data or [],
                context="find_all",
            )
        except Exception as e:
            logger.error(
                "Failed to query all brands: error_type=%s error=%s",
                type(e).__name__,
                str(e)[:300],
            )
            return list(self._in_memory.values())
