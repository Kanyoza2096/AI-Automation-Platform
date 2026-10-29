from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class OntologyNodeType(str, Enum):
    BUSINESS = "business"
    SECTOR = "sector"
    SERVICE = "service"
    CAPABILITY = "capability"
    PROCESS = "process"
    POLICY = "policy"
    CUSTOMER_SEGMENT = "customer_segment"
    LOCATION = "location"
    PAYMENT_METHOD = "payment_method"
    CONTACT_CHANNEL = "contact_channel"
    PRODUCT = "product"
    SYSTEM = "system"
    PROVIDER = "provider"
    PERSON_ROLE = "person_role"
    KNOWLEDGE_FACT = "knowledge_fact"


class OntologyEdgeType(str, Enum):
    HAS_SECTOR = "has_sector"
    OFFERS_SERVICE = "offers_service"
    HAS_CAPABILITY = "has_capability"
    SERVES_SEGMENT = "serves_segment"
    OPERATES_AT = "operates_at"
    ACCEPTS_PAYMENT = "accepts_payment"
    USES_CHANNEL = "uses_channel"
    HAS_POLICY = "has_policy"
    HAS_ROLE = "has_role"
    SUPPORTED_BY_FACT = "supported_by_fact"
    IMPLEMENTED_BY = "implemented_by"
    PROVIDED_BY = "provided_by"
    PART_OF = "part_of"


class OntologyNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(..., min_length=1)
    node_type: OntologyNodeType
    label: str = Field(..., min_length=1, max_length=256)
    workspace_id: str = Field(..., min_length=1)
    attributes: Dict[str, Any] = Field(default_factory=dict)
    source_fact_ids: List[str] = Field(default_factory=list)


class OntologyEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edge_id: str = Field(..., min_length=1)
    edge_type: OntologyEdgeType
    from_node_id: str = Field(..., min_length=1)
    to_node_id: str = Field(..., min_length=1)
    workspace_id: str = Field(..., min_length=1)
    source_fact_ids: List[str] = Field(default_factory=list)
    attributes: Dict[str, Any] = Field(default_factory=dict)


class BusinessOperatingOntology(BaseModel):
    """Canonical read model projected from existing KDO business knowledge.

    This is intentionally a projection, not a second knowledge store. Confirmed
    Knowledge Plane facts remain authoritative; this model only organizes them
    into business objects and relationships for reasoning and control-plane use.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "KDO.BusinessOperatingOntology/v1"
    workspace_id: str
    root_node_id: str
    nodes: List[OntologyNode] = Field(default_factory=list)
    edges: List[OntologyEdge] = Field(default_factory=list)
    source_fact_ids: List[str] = Field(default_factory=list)
    coverage: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)

    def node(self, node_id: str) -> Optional[OntologyNode]:
        return next((item for item in self.nodes if item.node_id == node_id), None)
