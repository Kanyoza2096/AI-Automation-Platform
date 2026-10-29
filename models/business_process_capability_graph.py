"""Canonical Business Process & Capability Graph read-model contracts.

The graph is a projection over existing KDO control-plane objects. It does not
create a second process engine, capability registry, action registry, or
provider registry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class GraphNodeType(str, Enum):
    BUSINESS = "business"
    SECTOR = "sector"
    SERVICE = "service"
    PROCESS = "process"
    WORKFLOW = "workflow"
    CAPABILITY = "capability"
    ACTION = "action"
    PROVIDER = "provider"
    POLICY = "policy"
    SYSTEM = "system"
    CUSTOMER_SEGMENT = "customer_segment"
    LOCATION = "location"
    PAYMENT_METHOD = "payment_method"
    CONTACT_CHANNEL = "contact_channel"
    PRODUCT = "product"
    PERSON_ROLE = "person_role"
    KNOWLEDGE_FACT = "knowledge_fact"


class GraphEdgeType(str, Enum):
    CONTAINS_PROCESS = "contains_process"
    CONTAINS_WORKFLOW = "contains_workflow"
    OFFERS_CAPABILITY = "offers_capability"
    ENABLES = "enables"
    REALIZED_BY_ACTION = "realized_by_action"
    PROVIDED_BY = "provided_by"
    IMPLEMENTED_BY = "implemented_by"
    DEPENDS_ON = "depends_on"
    GOVERNED_BY = "governed_by"
    PART_OF = "part_of"
    TRIGGERS = "triggers"
    SUPPORTS = "supports"
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


@dataclass(frozen=True)
class GraphNode:
    node_id: str
    node_type: GraphNodeType
    workspace_id: str
    name: str
    source: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphEdge:
    edge_id: str
    edge_type: GraphEdgeType
    workspace_id: str
    source_node_id: str
    target_node_id: str
    source: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BusinessProcessCapabilityGraph:
    schema: str
    workspace_id: str
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    warnings: tuple[str, ...] = ()
    coverage: Mapping[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "workspace_id": self.workspace_id,
            "nodes": [
                {"id": n.node_id, "type": n.node_type.value, "name": n.name,
                 "source": n.source, "metadata": dict(n.metadata)} for n in self.nodes
            ],
            "edges": [
                {"id": e.edge_id, "type": e.edge_type.value,
                 "source": e.source_node_id, "target": e.target_node_id,
                 "source_system": e.source, "metadata": dict(e.metadata)} for e in self.edges
            ],
            "warnings": list(self.warnings),
            "coverage": dict(self.coverage),
            "authority": {
                "read_only": True,
                "does_not_execute": True,
                "does_not_authorize": True,
                "does_not_register_capabilities": True,
                "does_not_register_actions": True,
                "does_not_register_providers": True,
            },
        }
