from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class FactType(str, Enum):
    BUSINESS_HOURS = "business_hours"
    REFUND_POLICY = "refund_policy"
    BUSINESS_IDENTITY = "business_identity"
    BUSINESS_DESCRIPTION = "business_description"
    PRODUCT_SERVICE = "product_service"
    LOCATION = "location"
    DELIVERY_AREA = "delivery_area"
    PAYMENT_METHOD = "payment_method"
    CONTACT_CHANNEL = "contact_channel"
    CUSTOMER_SEGMENT = "customer_segment"
    PRICING = "pricing"
    FULFILLMENT = "fulfillment"
    STAFF_ROLE = "staff_role"
    ESCALATION_RULE = "escalation_rule"
    INTEGRATION = "integration"
    FAQ = "faq"
    BUSINESS_RULE = "business_rule"
    BUSINESS_KNOWLEDGE = "business_knowledge"


FACT_TYPES = tuple(item.value for item in FactType)
# Broad activation mode: extract recognized structures and preserve other explicit
# business statements as reviewable knowledge rather than silently discarding them.
DEFAULT_BUSINESS_KNOWLEDGE_SCOPE = [FactType.BUSINESS_KNOWLEDGE]


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str = Field(default_factory=lambda: f"fact-{uuid4().hex}")
    workspace_id: str = Field(..., min_length=1)
    fact_type: FactType
    subject: str = Field(..., min_length=1, max_length=256)
    predicate: str = Field(..., min_length=1, max_length=128)
    value: Any
    conditions: Dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(..., ge=0.0, le=1.0)
    source: str = Field(..., pattern=r"^(operator_message|document|import|api)$")
    source_span: Dict[str, Any] = Field(...)
    asserted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    asserted_by: str = Field(..., min_length=1, description="Principal identifier; populated by authenticated request context")
    superseded_at: Optional[datetime] = None
    superseded_by: Optional[str] = None
    extraction_id: str = Field(..., min_length=1)
    confirmed: bool = False
    confirmed_at: Optional[datetime] = None

    def __getitem__(self, key: str):
        return getattr(self, key)


class Clarification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clarification_id: str = Field(default_factory=lambda: f"clar-{uuid4().hex}")
    fact_type: FactType
    field: str
    reason: str = Field(..., min_length=1, max_length=500)
    source_span: Dict[str, Any] = Field(...)


class ExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(..., min_length=1, max_length=128)
    operator_text: str = Field(..., min_length=1, max_length=12000)
    fact_types_in_scope: List[FactType] = Field(..., min_length=1, max_length=20)


class ReviewReextractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(..., min_length=1, max_length=128)
    operator_text: str = Field(..., min_length=1, max_length=12000)
    fact_type: FactType
    correction_text: str = Field(..., min_length=1, max_length=4000)


class ConfirmFactsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(..., min_length=1, max_length=128)
    extraction_id: str = Field(..., min_length=1)
    proposed_fact_ids: List[str] = Field(..., min_length=1, max_length=100)


class CurrentFactsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(..., min_length=1, max_length=128)
    fact_type: FactType
    subject: Optional[str] = None
    predicate: Optional[str] = None
    conditions: Optional[Dict[str, Any]] = None
    include_superseded: bool = False
    limit: int = Field(default=100, ge=1, le=100)


class BusinessKnowledgeReadQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(..., min_length=1, max_length=128)
    # A specific category gives a narrow read. business_knowledge is the
    # deliberate generalized wildcard and returns all currently supported
    # confirmed categories, including generic business statements.
    fact_type: Optional[FactType] = None
    subject: Optional[str] = None
    predicate: Optional[str] = None
    conditions: Optional[Dict[str, Any]] = None
    limit: int = Field(default=100, ge=1, le=500)


class BusinessAgentAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(..., min_length=1, max_length=128)
    question: str = Field(..., min_length=3, max_length=4000)
    # Optional explicit scope. If omitted, the runtime may only infer a scope
    # from an unambiguous deterministic question pattern.
    fact_type: Optional[FactType] = None


class BusinessAgentAnswerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    answerable: bool
    handoff_required: bool
    refusal_required: bool
    question: str
    answer: Optional[str] = None
    reason: Optional[str] = None
    knowledge_scope: Optional[str] = None
    workspace_id: str
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    fact_ids: List[str] = Field(default_factory=list)


class BusinessKnowledgeReadResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    answerable: bool
    handoff_required: bool
    refusal_required: bool
    reason: Optional[str] = None
    workspace_id: str
    facts: List[Fact]
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    count: int

    def __getitem__(self, key: str):
        # Compatibility for existing runtime adapters that consume this
        # contract as a mapping while the API exposes the typed model.
        return getattr(self, key)


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extraction_id: str
    proposed_facts: List[Fact]
    clarifications: List[Clarification]
    unparsed_text: List[str]
    extraction_confidence: float = Field(..., ge=0.0, le=1.0)
    model_used: str
    extracted_at: datetime
    input_fingerprint: str
