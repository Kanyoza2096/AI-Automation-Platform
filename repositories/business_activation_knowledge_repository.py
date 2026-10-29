from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Union

from domain.business_activation.facts import Fact, FactType

logger = logging.getLogger("kanyoza.repositories.business_activation_knowledge")


def _fact_type_value(fact_type: Union[FactType, str, None]) -> Optional[str]:
    """Coerce a fact_type input into its canonical string value.

    Callers within the codebase may pass either:
      - a FactType enum member (the strict contract), or
      - a raw string (e.g. "business_knowledge") coming from a boundary layer
        that did not run pydantic coercion.

    The repository must accept both. Anything else is treated as unknown and
    returned as None so the caller's query builder can skip the filter rather
    than crashing on an attribute access.
    """
    if fact_type is None:
        return None
    value = getattr(fact_type, "value", None)
    if isinstance(value, str) and value:
        return value
    if isinstance(fact_type, str) and fact_type:
        return fact_type
    return None


class BusinessActivationKnowledgeRepository:
    """Persistence boundary for confirmed Business Activation knowledge.

    This is a Knowledge Plane projection, not a second execution or authority
    system. Supabase is the durable source when configured; tests may use the
    deterministic in-memory implementation.

    V1.1 (2026-10-28) — FAIL-FAST ON SILENT EXTRACTION INSERT:

      The previous implementation called:

          self.supabase.table("business_activation_extractions").upsert(row).execute()
          for fact in result.proposed_facts:
              self.supabase.table("business_activation_facts").upsert(...).execute()

      When the extraction insert silently returned 0 rows (RLS block,
      transient connection issue, or upsert no-op), the loop proceeded
      anyway and the first fact insert raised a confusing FK violation:

          insert or update on table "business_activation_facts" violates
          foreign key constraint "business_activation_facts_extraction_id_fkey"

      That error misattributed the cause to the facts table. The real cause
      was upstream — the extraction row was never persisted.

      Fix:
        1. The extraction insert response is now checked for zero-row
           returns. If the extraction row did not persist, we raise a
           specific `extraction_insert_blocked` error BEFORE the facts
           insert can execute, preventing the FK violation entirely.
        2. Every insert (extraction + facts) is logged with the exact IDs
           and response lengths, so a future incident of this class is
           immediately visible in logs, not inferred from a downstream
           failure.
    """

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._facts: Dict[str, Fact] = {}
        self._extractions: Dict[str, Dict[str, Any]] = {}

    def record_extraction(self, result, operator_text: str, workspace_id: str, fact_types_in_scope: list[str]) -> None:
        row = {
            "extraction_id": result.extraction_id,
            "workspace_id": workspace_id,
            "operator_text": operator_text,
            "fact_types_in_scope": fact_types_in_scope,
            "proposed_facts": [f.model_dump(mode="json") for f in result.proposed_facts],
            "clarifications": [c.model_dump(mode="json") for c in result.clarifications],
            "unparsed_text": result.unparsed_text,
            "extraction_confidence": result.extraction_confidence,
            "model_used": result.model_used,
            "input_fingerprint": result.input_fingerprint,
            "extracted_at": result.extracted_at.isoformat(),
        }
        self._extractions[result.extraction_id] = row
        if not self.supabase:
            for fact in result.proposed_facts:
                self._facts[fact.fact_id] = fact.model_copy(
                    update={
                        "confirmed": False,
                        "confirmed_at": None,
                        "superseded_at": None,
                        "superseded_by": None,
                    }
                )
            return

        logger.info(
            "record_extraction: START extraction_id=%s workspace=%s facts=%d",
            result.extraction_id,
            workspace_id,
            len(result.proposed_facts),
        )

        # --- Step 1: persist the extraction row, MUST succeed ---------------
        try:
            extraction_response = self.supabase.table(
                "business_activation_extractions"
            ).upsert(row).execute()
            rows_returned = len(getattr(extraction_response, "data", []) or [])
            logger.info(
                "record_extraction: extraction upsert OK extraction_id=%s "
                "rows_returned=%d",
                result.extraction_id,
                rows_returned,
            )
        except Exception as exc:
            logger.error(
                "record_extraction: extraction upsert EXCEPTION "
                "extraction_id=%s workspace=%s error=%s",
                result.extraction_id,
                workspace_id,
                exc,
            )
            raise

        if rows_returned == 0:
            # Silent no-op. Common causes: RLS block on
            # business_activation_extractions, transient connection pool
            # issue, or the client is not the service-role singleton.
            # Do NOT proceed — the facts insert would FK-violate.
            logger.error(
                "record_extraction: extraction insert returned 0 rows — "
                "extraction_id=%s workspace=%s. Cannot insert facts "
                "(would violate FK). Investigate RLS policy on "
                "business_activation_extractions or Supabase client identity.",
                result.extraction_id,
                workspace_id,
            )
            raise RuntimeError(
                f"extraction_insert_blocked: extraction_id={result.extraction_id} "
                f"workspace={workspace_id}. The extraction row was not persisted. "
                f"Cannot insert facts without violating the FK constraint."
            )

        # --- Step 2: persist each fact --------------------------------------
        for fact in result.proposed_facts:
            try:
                self.supabase.table(
                    "business_activation_facts"
                ).upsert(fact.model_dump(mode="json")).execute()
                logger.debug(
                    "record_extraction: fact upsert OK fact_id=%s "
                    "extraction_id=%s",
                    fact.fact_id,
                    fact.extraction_id,
                )
            except Exception as exc:
                logger.error(
                    "record_extraction: fact upsert EXCEPTION fact_id=%s "
                    "extraction_id=%s error=%s",
                    fact.fact_id,
                    fact.extraction_id,
                    exc,
                )
                raise

        logger.info(
            "record_extraction: COMPLETE extraction_id=%s facts=%d",
            result.extraction_id,
            len(result.proposed_facts),
        )

    def confirm(self, *, workspace_id: str, extraction_id: str, proposed_fact_ids: List[str], asserted_by: str) -> tuple[List[Fact], bool]:
        if not self.supabase:
            existing = [f for f in self._facts.values() if f.workspace_id == workspace_id and f.extraction_id == extraction_id and f.confirmed]
            if existing:
                return existing, True
            source = self._extractions.get(extraction_id)
            if not source or source.get("workspace_id") not in (None, workspace_id):
                raise ValueError("extraction_not_found")
            proposed = [Fact(**row) for row in source["proposed_facts"] if row["fact_id"] in proposed_fact_ids]
            if len(proposed) != len(proposed_fact_ids):
                raise ValueError("proposed_fact_not_found")
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            for new_fact in proposed:
                for old in list(self._facts.values()):
                    if (old.workspace_id == workspace_id and old.confirmed and old.superseded_at is None and
                        old.fact_type == new_fact.fact_type and old.subject == new_fact.subject and
                        old.predicate == new_fact.predicate and old.conditions == new_fact.conditions):
                        old.superseded_at = now
                        old.superseded_by = new_fact.fact_id
                new_fact.confirmed = True
                new_fact.confirmed_at = now
                new_fact.asserted_by = asserted_by
                self._facts[new_fact.fact_id] = new_fact
            return proposed, False

        payload = {
            "p_workspace_id": workspace_id,
            "p_extraction_id": extraction_id,
            "p_proposed_fact_ids": proposed_fact_ids,
            "p_asserted_by": asserted_by,
        }
        result = self.supabase.rpc("confirm_business_activation_facts", payload).execute()
        rows = result.data or []
        facts = [Fact(**row) for row in rows]
        replay = bool(facts) and all(f.extraction_id == extraction_id for f in facts)
        # The SQL function returns the same confirmed rows on replay; callers
        # determine replay state from the extraction confirmation marker.
        marker = self.supabase.table("business_activation_extractions").select("confirmed_at").eq("extraction_id", extraction_id).eq("workspace_id", workspace_id).limit(1).execute()
        replay = replay and bool(marker.data and marker.data[0].get("confirmed_at"))
        return facts, replay

    def current(
        self,
        *,
        workspace_id: str,
        fact_type: Optional[Union[FactType, str]] = None,
        subject: Optional[str] = None,
        predicate: Optional[str] = None,
        conditions: Optional[Dict[str, Any]] = None,
        include_superseded: bool = False,
        limit: int = 100,
    ) -> List[Fact]:
        """Read current (confirmed, non-superseded by default) facts.

        fact_type may be passed as either a FactType enum or a raw string.
        The repository coerces to the canonical string value before use, so
        callers at different layers do not need to know the internal
        representation.
        """
        fact_type_value = _fact_type_value(fact_type)

        if not self.supabase:
            rows = [f for f in self._facts.values() if f.workspace_id == workspace_id and f.confirmed]
            if fact_type_value is not None:
                rows = [f for f in rows if f.fact_type.value == fact_type_value]
            if subject is not None:
                rows = [f for f in rows if f.subject == subject]
            if predicate is not None:
                rows = [f for f in rows if f.predicate == predicate]
            if conditions is not None:
                rows = [f for f in rows if f.conditions == conditions]
            if not include_superseded:
                rows = [f for f in rows if f.superseded_at is None]
            return sorted(rows, key=lambda f: (f.confirmed_at or f.asserted_at), reverse=True)[:limit]

        q = self.supabase.table("business_activation_facts").select("*").eq("workspace_id", workspace_id).eq("confirmed", True)
        if fact_type_value is not None:
            q = q.eq("fact_type", fact_type_value)
        if subject is not None:
            q = q.eq("subject", subject)
        if predicate is not None:
            q = q.eq("predicate", predicate)
        if conditions is not None:
            q = q.eq("conditions", conditions)
        if not include_superseded:
            q = q.is_("superseded_at", "null")
        result = q.order("confirmed_at", desc=True).limit(limit).execute()
        return [Fact(**row) for row in (result.data or [])]
