"""
KnowledgeBase Service.
Provides a per-brand knowledge store with lightweight TF-IDF semantic search.
Uses only standard library modules to calculate text similarity.
"""

from __future__ import annotations

import math
import re
import uuid
import json
import logging
from typing import List, Dict, Tuple, Optional, Any

from models.knowledge_entry import KnowledgeEntry
from repositories.knowledge_repository import KnowledgeRepository

logger = logging.getLogger("kanyoza.services.knowledge_base")

def tokenize(text: str) -> List[str]:
    """Tokenize text into lowercased words."""
    return re.findall(r"\w+", text.lower())


class KnowledgeBase:
    """Manages brand knowledge entries and performs TF-IDF search."""

    def __init__(self, brand_id: str, workspace_id: str, repository: KnowledgeRepository):
        self.brand_id = brand_id
        self.workspace_id = workspace_id
        self.repo = repository

    def add_document(self, content: str, doc_type: str = "text", metadata: Optional[Dict] = None) -> KnowledgeEntry:
        """Stores a new knowledge entry and triggers indexing."""
        entry_id = f"kb-{uuid.uuid4().hex[:12]}"
        
        # Create entity (we will calculate embeddings during query time or store precalculated token counts)
        # For simplicity, we just store the raw document.
        # When pgvector or Gemini embeddings are integrated, we would populate embedding_json.
        entry = KnowledgeEntry(
            id=entry_id,
            workspace_id=self.workspace_id,
            brand_id=self.brand_id,
            content=content,
            doc_type=doc_type,
            embedding_json="[]",
            metadata=metadata or {}
        )
        self.repo.save(entry)
        return entry

    def search(self, query: str, top_k: int = 5) -> List[Tuple[KnowledgeEntry, float]]:
        """
        Lightweight semantic search using TF-IDF + Cosine Similarity.
        No numpy or external dependencies.
        """
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        entries = self.repo.find_by_brand(self.brand_id)
        if not entries:
            return []

        # 1. Compute Document frequencies (DF) for IDF calculation
        # DF tracks how many documents contain each word
        df: Dict[str, int] = {}
        doc_tokens_list: List[Tuple[KnowledgeEntry, List[str]]] = []
        
        for entry in entries:
            tokens = tokenize(entry.content)
            if not tokens:
                continue
            doc_tokens_list.append((entry, tokens))
            # Track unique terms in this document
            unique_terms = set(tokens)
            for term in unique_terms:
                df[term] = df.get(term, 0) + 1

        num_docs = len(doc_tokens_list)
        if num_docs == 0:
            return []

        # Helper to compute IDF
        # math.log(1 + total_docs / (1 + doc_containing_token))
        def idf(term: str) -> float:
            return math.log(1.0 + num_docs / (1.0 + df.get(term, 0)))

        # 2. Vectorize the query
        # Query Term Frequency (TF)
        query_tf: Dict[str, float] = {}
        for token in query_tokens:
            query_tf[token] = query_tf.get(token, 0.0) + 1.0
        
        # Apply IDF to query
        query_vector: Dict[str, float] = {}
        query_norm_sq = 0.0
        for token, count in query_tf.items():
            # count / len(query_tokens)
            tf_val = count / len(query_tokens)
            idf_val = idf(token)
            val = tf_val * idf_val
            query_vector[token] = val
            query_norm_sq += val * val
        
        query_norm = math.sqrt(query_norm_sq)
        if query_norm == 0.0:
            return []

        results: List[Tuple[KnowledgeEntry, float]] = []

        # 3. Vectorize documents and calculate cosine similarity
        for entry, doc_tokens in doc_tokens_list:
            doc_tf: Dict[str, float] = {}
            for token in doc_tokens:
                doc_tf[token] = doc_tf.get(token, 0.0) + 1.0
            
            doc_vector: Dict[str, float] = {}
            doc_norm_sq = 0.0
            for token, count in doc_tf.items():
                tf_val = count / len(doc_tokens)
                idf_val = idf(token)
                val = tf_val * idf_val
                doc_vector[token] = val
                doc_norm_sq += val * val
            
            doc_norm = math.sqrt(doc_norm_sq)
            if doc_norm == 0.0:
                continue

            # Cosine similarity dot product
            dot_product = 0.0
            for token in query_vector:
                if token in doc_vector:
                    dot_product += query_vector[token] * doc_vector[token]
            
            similarity = dot_product / (query_norm * doc_norm)
            if similarity > 0.0:
                results.append((entry, similarity))

        # Sort by similarity score descending
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]
