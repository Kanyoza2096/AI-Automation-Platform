"""
Knowledge Entry domain model.
Represents a piece of brand knowledge (e.g. facts, product descriptions, topic banks)
used during prompt generation.

V1.1 — embedding_json coercion.
  The knowledge_entries.embedding_json column may be jsonb (Supabase returns
  a parsed Python list, e.g. []) or text (returns a string, e.g. "[]").
  The bulk ingestion writes "[]" as a string, but if the column is jsonb
  PostgREST returns [] as a list. Pydantic then rejects the row with
  "Input should be a valid string [type=string_type, input_value=[]]".

  That validation failure cascaded: find_by_brand() returned an empty list
  for every brand whose corpus was bulk-ingested, TopicSelector could not
  build categories, and the pipeline fell back to platform-wide fallback
  topics ("product tip"). The model then invented metrics to satisfy the
  "be specific" prompt — the hallucination chain.

  The validator below accepts None, str, and list, and always returns a
  JSON-encoded string. Existing text-column rows are unaffected (a string
  passes through unchanged).

V1.2 — title field.
  The corpus stores the human-readable title in metadata.title, not on the
  model. But several consumers use getattr(entry, "title", None) which
  returns None for these rows. Surfacing `title` as a first-class field
  lets callers read it uniformly; it defaults to "" and is populated from
  metadata.title by the ingestion path if present.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import Field, field_validator

from models.base import KanyozaModel


class KnowledgeEntry(KanyozaModel):
    """
    A single factual document or topic bank element linked to a brand.
    Used for semantic retrieval during the content generation pipeline.
    """

    id: str = Field(..., min_length=1, description="Unique entry identifier")
    workspace_id: str = Field(..., min_length=1, description="Owning workspace")
    brand_id: str = Field(..., min_length=1, description="Owning brand")
    content: str = Field(..., min_length=1, description="Factual text content")
    doc_type: str = Field(default="text", description="Document type: text, url, file, topic")

    # V1.2 — first-class title field. Populated from metadata.title during
    # ingestion when present. Defaults to "" so getattr(entry, "title", None)
    # never returns None for a well-formed row.
    title: str = Field(default="", max_length=500, description="Human-readable title, if any")

    embedding_json: Optional[str] = Field(
        default="[]",
        description="JSON string representation of vector/TF-IDF values",
    )

    # Metadata
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = Field(default=None)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # ═══════════════════════════════════════════════════════════════════════
    # Validators
    # ═══════════════════════════════════════════════════════════════════════

    @field_validator("embedding_json", mode="before")
    @classmethod
    def _coerce_embedding_json(cls, v: Any) -> str:
        """Coerce the DB value to a JSON-encoded string.

        Accepts:
          - None        → "[]"
          - "[]"        → "[]" (pass-through; text column)
          - []          → "[]" (coerce; jsonb column)
          - [0.1, 0.2]  → "[0.1, 0.2]"
          - {"a": 1}    → '{"a": 1}'

        The column may be either jsonb or text depending on the ingestion
        path. Both shapes must hydrate to the same Python type so that
        find_by_brand() does not reject the row.
        """
        if v is None:
            return "[]"
        if isinstance(v, str):
            # Already a string — trust it. Empty/whitespace becomes "[]".
            stripped = v.strip()
            return stripped or "[]"
        if isinstance(v, (list, dict)):
            return json.dumps(v)
        # Anything else (tuple, set, numpy array, etc.) → best-effort JSON.
        try:
            return json.dumps(v)
        except Exception:
            return "[]"

    @field_validator("title", mode="before")
    @classmethod
    def _coerce_title(cls, v: Any) -> str:
        """Coerce title to a string, tolerating None and non-string inputs."""
        if v is None:
            return ""
        return str(v).strip()

    @field_validator("metadata", mode="before")
    @classmethod
    def _coerce_metadata(cls, v: Any) -> Dict[str, Any]:
        """Coerce metadata to a dict, tolerating None / JSON-encoded string.

        Some Supabase configurations return JSONB columns as JSON-encoded
        strings rather than parsed dicts. The bulk-ingestion path writes
        dicts, but reads may return strings on some driver versions.
        """
        if v is None:
            return {}
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            stripped = v.strip()
            if not stripped:
                return {}
            try:
                parsed = json.loads(stripped)
            except Exception:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}
