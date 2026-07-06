"""
Knowledge Entry domain model.
Represents a piece of brand knowledge (e.g. facts, product descriptions, topic banks)
used during prompt generation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import Field

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
    embedding_json: Optional[str] = Field(default="[]", description="JSON string representation of vector/TF-IDF values")
    
    # Metadata
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = Field(default=None)
    metadata: Dict[str, Any] = Field(default_factory=dict)
