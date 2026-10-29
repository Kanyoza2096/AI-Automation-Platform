from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Iterable, List

from domain.business_activation.facts import Fact, FactType
from models.business_operating_ontology import (
    BusinessOperatingOntology,
    OntologyEdge,
    OntologyEdgeType,
    OntologyNode,
    OntologyNodeType,
)


class BusinessOperatingOntologyProjector:
    """Build a deterministic ontology projection from existing confirmed facts.

    No persistence, confirmation, authorization, provider binding, or execution
    occurs here. The Knowledge Plane remains the source of truth.
    """

    def project(self, workspace_id: str, facts: Iterable[Fact]) -> BusinessOperatingOntology:
        confirmed = sorted(
            (fact for fact in facts
             if fact.workspace_id == workspace_id and fact.confirmed and fact.superseded_at is None),
            key=lambda item: item.fact_id,
        )
        nodes: dict[str, OntologyNode] = {}
        edges: dict[str, OntologyEdge] = {}
        source_ids: set[str] = set()

        root_id = self._id(workspace_id, "business", workspace_id)
        nodes[root_id] = OntologyNode(
            node_id=root_id,
            node_type=OntologyNodeType.BUSINESS,
            label="Business",
            workspace_id=workspace_id,
            attributes={"workspace_id": workspace_id},
        )

        for fact in confirmed:
            source_ids.add(fact.fact_id)
            self._project_fact(workspace_id, fact, nodes, edges)

        for edge in edges.values():
            source_ids.update(edge.source_fact_ids)

        coverage = self._coverage(confirmed)
        warnings: List[str] = []
        if not confirmed:
            warnings.append("ontology_has_no_confirmed_business_facts")
        if coverage.get("business_identity", 0) == 0:
            warnings.append("business_identity_is_not_confirmed")
        if coverage.get("product_service", 0) == 0:
            warnings.append("no_confirmed_products_or_services")

        return BusinessOperatingOntology(
            workspace_id=workspace_id,
            root_node_id=root_id,
            nodes=list(nodes.values()),
            edges=list(edges.values()),
            source_fact_ids=sorted(source_ids),
            coverage=coverage,
            warnings=warnings,
        )

    def _project_fact(self, workspace_id, fact, nodes, edges) -> None:
        root_id = self._id(workspace_id, "business", workspace_id)
        value = fact.value
        text = self._value_text(value)

        mapping = {
            FactType.PRODUCT_SERVICE: (OntologyNodeType.SERVICE, OntologyEdgeType.OFFERS_SERVICE),
            FactType.LOCATION: (OntologyNodeType.LOCATION, OntologyEdgeType.OPERATES_AT),
            FactType.PAYMENT_METHOD: (OntologyNodeType.PAYMENT_METHOD, OntologyEdgeType.ACCEPTS_PAYMENT),
            FactType.CONTACT_CHANNEL: (OntologyNodeType.CONTACT_CHANNEL, OntologyEdgeType.USES_CHANNEL),
            FactType.CUSTOMER_SEGMENT: (OntologyNodeType.CUSTOMER_SEGMENT, OntologyEdgeType.SERVES_SEGMENT),
            FactType.STAFF_ROLE: (OntologyNodeType.PERSON_ROLE, OntologyEdgeType.HAS_ROLE),
            FactType.REFUND_POLICY: (OntologyNodeType.POLICY, OntologyEdgeType.HAS_POLICY),
            FactType.BUSINESS_RULE: (OntologyNodeType.POLICY, OntologyEdgeType.HAS_POLICY),
            FactType.ESCALATION_RULE: (OntologyNodeType.POLICY, OntologyEdgeType.HAS_POLICY),
            FactType.INTEGRATION: (OntologyNodeType.SYSTEM, OntologyEdgeType.IMPLEMENTED_BY),
        }
        node_type, edge_type = mapping.get(fact.fact_type, (OntologyNodeType.KNOWLEDGE_FACT, OntologyEdgeType.SUPPORTED_BY_FACT))
        label = self._label(fact, text)
        node_id = self._id(workspace_id, node_type.value, f"{fact.subject}:{fact.predicate}:{text}")
        existing = nodes.get(node_id)
        if existing:
            if fact.fact_id not in existing.source_fact_ids:
                existing.source_fact_ids.append(fact.fact_id)
            return

        nodes[node_id] = OntologyNode(
            node_id=node_id,
            node_type=node_type,
            label=label,
            workspace_id=workspace_id,
            attributes={
                "subject": fact.subject,
                "predicate": fact.predicate,
                "value": value,
                "conditions": fact.conditions,
                "confidence": fact.confidence,
            },
            source_fact_ids=[fact.fact_id],
        )
        edge_id = self._id(workspace_id, "edge", f"{root_id}:{edge_type.value}:{node_id}")
        edges[edge_id] = OntologyEdge(
            edge_id=edge_id,
            edge_type=edge_type,
            from_node_id=root_id,
            to_node_id=node_id,
            workspace_id=workspace_id,
            source_fact_ids=[fact.fact_id],
        )

    @staticmethod
    def _coverage(facts: List[Fact]) -> dict:
        counts = defaultdict(int)
        for fact in facts:
            counts[fact.fact_type.value] += 1
        return dict(sorted(counts.items()))

    @staticmethod
    def _value_text(value) -> str:
        if isinstance(value, dict):
            if "name" in value:
                return str(value["name"])
            if "text" in value:
                return str(value["text"])
            return ", ".join(f"{k}={v}" for k, v in sorted(value.items()))
        if isinstance(value, (list, tuple, set)):
            return ", ".join(str(item) for item in value)
        return str(value)

    @staticmethod
    def _label(fact: Fact, value_text: str) -> str:
        if fact.fact_type is FactType.BUSINESS_IDENTITY:
            return value_text
        return value_text[:256] or fact.subject

    @staticmethod
    def _id(workspace_id: str, kind: str, value: str) -> str:
        digest = hashlib.sha256(f"{workspace_id}|{kind}|{value}".encode()).hexdigest()[:20]
        return f"bo-{kind}-{digest}"
