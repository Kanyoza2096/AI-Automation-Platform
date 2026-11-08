"""
KnowledgeBase Service v3.1 — Per-brand knowledge store with semantic search.

v3.1 CHANGES:
  - Brand theme lookup (get_brand_themes / aget_brand_themes) drives topic
    axis rotation. The taxonomy comes from knowledge_entries.metadata.theme,
    not from a hardcoded list.
  - Theme cache has a TTL (a script that writes straight to the DB, or a
    second worker process, can no longer leave it stale forever) and is
    invalidated on add_document AND remove_document.
  - Theme query paginates (Supabase returns at most ~1000 rows per request).
  - asyncio.get_running_loop() replaces the deprecated get_event_loop().
  - _rebuild_index clears the index even when the brand has no entries left.
  - _add_to_index no longer double-counts a re-added entry id.

v3.0: asyncio.Lock, async repository calls, inverted index, TF-IDF,
topic extraction (all preserved).
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import threading
import time
import uuid
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from models.knowledge_entry import KnowledgeEntry
from repositories.knowledge_repository import KnowledgeRepository

logger = logging.getLogger("kanyoza.services.knowledge_base")


# ═══════════════════════════════════════════════════════════════════════════
# UTILITIES (pure functions)
# ═══════════════════════════════════════════════════════════════════════════

def tokenize(text: str) -> List[str]:
    return [token for token in re.findall(r"\w+", text.lower()) if token not in STOP_WORDS]


STOP_WORDS = {
    "the", "is", "at", "which", "on", "and", "a", "an",
    "in", "to", "for", "of", "with", "by", "from", "as",
    "this", "that", "it", "or", "are", "was", "be", "been",
    "were", "has", "have", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might",
    "can", "shall", "not", "no", "but", "if", "so", "we",
    "you", "he", "she", "they", "them", "their", "our",
    "its", "just", "also", "very", "too", "all", "some",
    "any", "each", "every", "both", "few", "more", "most",
    "other", "such", "only", "own", "same", "than",
    "about", "after", "before", "between", "during",
    "through", "above", "below", "up", "down", "out",
    "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why",
    "how", "what", "who", "whom",
}


def _term_similarity(a: str, b: str) -> float:
    a_set = set(a)
    b_set = set(b)
    if not a_set or not b_set:
        return 0.0
    intersection = a_set & b_set
    return len(intersection) / max(len(a_set), len(b_set))


# ═══════════════════════════════════════════════════════════════════════════
# BRAND THEME CACHE
# ═══════════════════════════════════════════════════════════════════════════
#
# get_brand_themes() derives the topic-axis rotation pool from the themes
# actually present in knowledge_entries (metadata.theme). Adding an entry
# with theme="baptism" adds "baptism" to the pool with no code change.
#
_THEMES_CACHE_TTL_SECONDS = 600.0
_THEMES_PAGE_SIZE = 500
_THEME_DESCRIPTION_CHARS = 100

# key -> (stored_at_monotonic, themes)
_BRAND_THEMES_CACHE: Dict[Tuple[str, str], Tuple[float, Dict[str, str]]] = {}
_BRAND_THEMES_CACHE_LOCK = threading.Lock()


def _themes_cache_key(
    brand_id: Optional[str], workspace_id: Optional[str]
) -> Tuple[str, str]:
    return (str(workspace_id or ""), str(brand_id or ""))


def get_brand_themes(
    brand_id: Optional[str], workspace_id: Optional[str]
) -> Dict[str, str]:
    """Return {theme: sample_description} for the brand's KB.

    Synchronous and may hit the database on a cache miss. From async code,
    use aget_brand_themes() so the event loop is not blocked.

    The description is the first ~100 characters of the first entry tagged
    with that theme (prompt context only). Cached per (workspace, brand)
    for _THEMES_CACHE_TTL_SECONDS or until invalidate_brand_themes_cache().
    Fails soft: any error returns {} (and is not cached).
    """
    if not workspace_id:
        return {}

    key = _themes_cache_key(brand_id, workspace_id)
    now = time.monotonic()
    with _BRAND_THEMES_CACHE_LOCK:
        cached = _BRAND_THEMES_CACHE.get(key)
        if cached is not None and (now - cached[0]) < _THEMES_CACHE_TTL_SECONDS:
            return dict(cached[1])

    try:
        from services.supabase import supabase_service

        client = supabase_service.service_client or supabase_service.client
        if client is None:
            return {}

        themes: Dict[str, str] = {}
        offset = 0
        while True:
            query = (
                client.table("knowledge_entries")
                .select("id, metadata, content")
                .eq("workspace_id", workspace_id)
            )
            if brand_id:
                query = query.eq("brand_id", brand_id)
            result = (
                query.order("id")
                .range(offset, offset + _THEMES_PAGE_SIZE - 1)
                .execute()
            )
            rows = result.data or []

            for row in rows:
                meta = row.get("metadata") or {}
                theme = str(meta.get("theme") or "").strip()
                if theme and theme not in themes:
                    themes[theme] = (row.get("content") or "")[
                        :_THEME_DESCRIPTION_CHARS
                    ]

            if len(rows) < _THEMES_PAGE_SIZE:
                break
            offset += _THEMES_PAGE_SIZE

        with _BRAND_THEMES_CACHE_LOCK:
            _BRAND_THEMES_CACHE[key] = (time.monotonic(), dict(themes))

        logger.info(
            "KnowledgeBase: loaded %d distinct themes for brand=%s workspace=%s",
            len(themes), brand_id or "(workspace)", workspace_id,
        )
        return themes
    except Exception as exc:
        logger.warning("get_brand_themes failed: %s", exc)
        return {}


async def aget_brand_themes(
    brand_id: Optional[str], workspace_id: Optional[str]
) -> Dict[str, str]:
    """Async wrapper: runs get_brand_themes in the default executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, get_brand_themes, brand_id, workspace_id
    )


def invalidate_brand_themes_cache(
    brand_id: Optional[str], workspace_id: Optional[str]
) -> None:
    """Drop the cached themes for one brand (and the workspace-wide entry)."""
    with _BRAND_THEMES_CACHE_LOCK:
        _BRAND_THEMES_CACHE.pop(_themes_cache_key(brand_id, workspace_id), None)
        _BRAND_THEMES_CACHE.pop(_themes_cache_key(None, workspace_id), None)


# ═══════════════════════════════════════════════════════════════════════════
# KNOWLEDGE BASE v3.1 — ASYNC-NATIVE
# ═══════════════════════════════════════════════════════════════════════════

class KnowledgeBase:
    """Manages brand knowledge entries with TF-IDF semantic search.

    Call `await kb.initialize()` after construction to build the index.
    """

    def __init__(
        self,
        brand_id: str,
        workspace_id: str,
        repository: KnowledgeRepository,
    ):
        self.brand_id = brand_id
        self.workspace_id = workspace_id
        self.repo = repository

        self._inverted_index: Dict[str, set] = defaultdict(set)
        self._idf_cache: Dict[str, float] = {}
        self._num_docs = 0
        self._doc_tokens: Dict[str, List[str]] = {}
        self._entries: Dict[str, KnowledgeEntry] = {}

        self._lock = asyncio.Lock()

        self._search_count = 0
        self._index_dirty = False

    # ── Initialization ────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Async initialization — builds index from repository."""
        await self._rebuild_index()

    # ── Index management ──────────────────────────────────────────────

    async def _rebuild_index(self) -> None:
        workspace_id = str(self.workspace_id or "").strip()
        if not workspace_id or workspace_id.lower() in {"default", "unbound", "platform"}:
            raise RuntimeError(
                "an active tenant workspace_id is required to build a knowledge index"
            )
        loop = asyncio.get_running_loop()
        entries = await loop.run_in_executor(
            None, self.repo.find_by_brand, self.brand_id, workspace_id
        )

        async with self._lock:
            self._inverted_index.clear()
            self._doc_tokens.clear()
            self._entries.clear()
            self._idf_cache.clear()

            for entry in entries or []:
                tokens = tokenize(entry.content or "")
                if not tokens:
                    continue

                self._entries[entry.id] = entry
                self._doc_tokens[entry.id] = tokens

                for term in set(tokens):
                    self._inverted_index[term].add(entry.id)

            self._num_docs = len(self._doc_tokens)

            for term, doc_ids in self._inverted_index.items():
                self._idf_cache[term] = math.log(
                    1.0 + self._num_docs / (1.0 + len(doc_ids))
                )

            self._index_dirty = False

        logger.info(
            "KnowledgeBase: index built for brand=%s — %d docs, %d terms",
            self.brand_id, self._num_docs, len(self._inverted_index),
        )

    async def _add_to_index(self, entry: KnowledgeEntry) -> None:
        tokens = tokenize(entry.content or "")
        if not tokens:
            return

        async with self._lock:
            # Re-adding an existing id: drop its old postings first.
            old_tokens = self._doc_tokens.get(entry.id)
            if old_tokens:
                for term in set(old_tokens):
                    postings = self._inverted_index.get(term)
                    if postings is not None:
                        postings.discard(entry.id)
                        if not postings:
                            del self._inverted_index[term]

            self._entries[entry.id] = entry
            self._doc_tokens[entry.id] = tokens
            self._num_docs = len(self._doc_tokens)

            for term in set(tokens):
                self._inverted_index[term].add(entry.id)

            self._idf_cache.clear()
            self._index_dirty = True

    # ── CRUD ──────────────────────────────────────────────────────────

    async def add_document(
        self,
        content: str,
        doc_type: str = "text",
        metadata: Optional[Dict] = None,
    ) -> KnowledgeEntry:
        entry_id = f"kb-{uuid.uuid4().hex[:12]}"

        entry = KnowledgeEntry(
            id=entry_id,
            workspace_id=self.workspace_id,
            brand_id=self.brand_id,
            content=content,
            doc_type=doc_type,
            embedding_json="[]",
            metadata=metadata or {},
        )

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.repo.save, entry)
        await self._add_to_index(entry)
        invalidate_brand_themes_cache(self.brand_id, self.workspace_id)

        logger.info("KnowledgeBase: added document %s", entry_id)
        return entry

    async def add_documents_batch(
        self, documents: List[Dict[str, Any]]
    ) -> List[KnowledgeEntry]:
        entries = []
        for doc in documents:
            entry = await self.add_document(
                content=doc.get("content", ""),
                doc_type=doc.get("doc_type", "text"),
                metadata=doc.get("metadata"),
            )
            entries.append(entry)
        return entries

    async def remove_document(self, entry_id: str) -> bool:
        loop = asyncio.get_running_loop()
        entry = await loop.run_in_executor(None, self.repo.find_by_id, entry_id)
        if not entry:
            return False

        await loop.run_in_executor(None, self.repo.delete, entry_id)
        await self._rebuild_index()
        invalidate_brand_themes_cache(self.brand_id, self.workspace_id)
        return True

    # ── Search ────────────────────────────────────────────────────────

    async def search(
        self, query: str, top_k: int = 5
    ) -> List[Tuple[KnowledgeEntry, float]]:
        if self._index_dirty:
            await self._rebuild_index()

        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        async with self._lock:
            if self._num_docs == 0:
                return []

            # Candidates via inverted index
            candidate_ids: set = set()
            for token in query_tokens:
                if token in self._inverted_index:
                    candidate_ids.update(self._inverted_index[token])

            if not candidate_ids:
                return []

            candidates = [
                (self._entries[eid], self._doc_tokens[eid])
                for eid in candidate_ids
                if eid in self._entries and eid in self._doc_tokens
            ]

            if not candidates:
                return []

            def idf(term: str) -> float:
                return self._idf_cache.get(
                    term,
                    math.log(
                        1.0 + self._num_docs
                        / (1.0 + len(self._inverted_index.get(term, set())))
                    ),
                )

            # Query vector
            query_tf: Dict[str, float] = {}
            for token in query_tokens:
                query_tf[token] = query_tf.get(token, 0.0) + 1.0

            query_vector: Dict[str, float] = {}
            query_norm_sq = 0.0
            for token, count in query_tf.items():
                val = (count / len(query_tokens)) * idf(token)
                query_vector[token] = val
                query_norm_sq += val * val

            query_norm = math.sqrt(query_norm_sq)
            if query_norm == 0.0:
                return []

            # Score candidates
            results = []
            for entry, doc_tokens in candidates:
                doc_tf: Dict[str, float] = {}
                for token in doc_tokens:
                    doc_tf[token] = doc_tf.get(token, 0.0) + 1.0

                doc_norm_sq = 0.0
                doc_vector: Dict[str, float] = {}
                for token, count in doc_tf.items():
                    val = (count / len(doc_tokens)) * idf(token)
                    doc_vector[token] = val
                    doc_norm_sq += val * val

                doc_norm = math.sqrt(doc_norm_sq)
                if doc_norm == 0.0:
                    continue

                dot_product = sum(
                    query_vector[token] * doc_vector[token]
                    for token in query_vector
                    if token in doc_vector
                )

                similarity = dot_product / (query_norm * doc_norm)
                if similarity > 0.0:
                    results.append((entry, similarity))

            self._search_count += 1
            results.sort(key=lambda x: x[1], reverse=True)
            return results[:top_k]

    # ── Topics ────────────────────────────────────────────────────────

    async def extract_topics(self, top_n: int = 10) -> List[Tuple[str, float]]:
        if self._index_dirty:
            await self._rebuild_index()

        async with self._lock:
            all_tokens = [
                t for tokens in self._doc_tokens.values() for t in tokens
            ]
            idf_snapshot = dict(self._idf_cache)

        if not all_tokens:
            return []

        total = len(all_tokens)
        tf: Dict[str, float] = {}
        for token in all_tokens:
            tf[token] = tf.get(token, 0.0) + 1.0

        scored = []
        for term, count in tf.items():
            if len(term) < 4 or term in STOP_WORDS or term.isdigit():
                continue
            tf_val = count / total
            idf_val = idf_snapshot.get(term, 0.0)
            length_bonus = math.log(len(term) + 1)
            scored.append((term, round(tf_val * idf_val * length_bonus, 6)))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_n]

    async def get_topic_suggestions(self, top_n: int = 5) -> List[str]:
        topics = await self.extract_topics(top_n=top_n * 2)
        return [t for t, _ in topics[:top_n]]

    # ── Health & stats ────────────────────────────────────────────────

    def get_stats(self) -> dict:
        return {
            "brand_id": self.brand_id,
            "documents": self._num_docs,
            "unique_terms": len(self._inverted_index),
            "searches_performed": self._search_count,
            "idf_cached": len(self._idf_cache) > 0,
            "index_dirty": self._index_dirty,
        }

    def health_check(self) -> dict:
        stats = self.get_stats()
        status = "healthy"
        if stats["documents"] == 0:
            status = "empty"
        elif stats["index_dirty"]:
            status = "degraded"
        return {"status": status, "stats": stats}


__all__ = [
    "KnowledgeBase",
    "tokenize",
    "STOP_WORDS",
    "get_brand_themes",
    "aget_brand_themes",
    "invalidate_brand_themes_cache",
]
