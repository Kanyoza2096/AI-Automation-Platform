"""Phase 2 — canonical Business Process & Capability Graph projection.

This is a deterministic read model over KDO's existing ontology, process
orchestrator, capability registry, Sector Platform and Connector Fabric.
No source system is mutated and no semantic relationship is guessed from
names. Cross-object edges require an explicit declaration in source metadata.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from models.business_process_capability_graph import (
    BusinessProcessCapabilityGraph,
    GraphEdge,
    GraphEdgeType,
    GraphNode,
    GraphNodeType,
)


SCHEMA = "KDO.BusinessProcessCapabilityGraph/v1"


def _id(prefix: str, *parts: object) -> str:
    raw = json.dumps([str(x) for x in parts], separators=(",", ":"), ensure_ascii=True)
    return f"{prefix}_{hashlib.sha256(raw.encode()).hexdigest()[:20]}"


def _metadata_value(metadata: Mapping[str, Any], *keys: str) -> list[str]:
    values: list[str] = []
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
        elif isinstance(value, (list, tuple, set)):
            values.extend(str(v).strip() for v in value if str(v).strip())
    return list(dict.fromkeys(values))


class BusinessProcessCapabilityGraphProjector:
    """Project existing KDO contracts into one tenant-scoped graph."""

    def project(
        self,
        *,
        workspace_id: str,
        ontology: Any | None = None,
        process_definitions: Iterable[Any] = (),
        capability_specs: Iterable[Any] = (),
        sector_platform: Mapping[str, Any] | None = None,
        connector_fabric: Mapping[str, Any] | None = None,
    ) -> BusinessProcessCapabilityGraph:
        if not str(workspace_id).strip():
            raise ValueError("workspace_id is required")

        nodes: dict[str, GraphNode] = {}
        edges: dict[str, GraphEdge] = {}
        warnings: list[str] = []

        def add_node(node: GraphNode) -> str:
            existing = nodes.get(node.node_id)
            if existing is None:
                nodes[node.node_id] = node
            return node.node_id

        def add_edge(edge_type: GraphEdgeType, source_id: str, target_id: str, source: str, metadata: Mapping[str, Any] | None = None) -> None:
            if source_id not in nodes or target_id not in nodes:
                return
            edge_id = _id("edge", workspace_id, edge_type.value, source_id, target_id, json.dumps(dict(metadata or {}), sort_keys=True, default=str))
            edges[edge_id] = GraphEdge(edge_id, edge_type, workspace_id, source_id, target_id, source, dict(metadata or {}))

        # Reuse Phase 1 ontology node identities instead of creating duplicates.
        if ontology is not None:
            if getattr(ontology, "workspace_id", workspace_id) != workspace_id:
                warnings.append("ontology_workspace_mismatch")
            else:
                for n in getattr(ontology, "nodes", ()):
                    try:
                        node_type = GraphNodeType(n.node_type.value)
                    except ValueError:
                        continue
                    add_node(GraphNode(n.node_id, node_type, workspace_id, n.label, "business_operating_ontology", {**dict(n.attributes), "source_fact_ids": list(n.source_fact_ids)}))
                for e in getattr(ontology, "edges", ()):
                    try:
                        edge_type = GraphEdgeType(e.edge_type.value)
                    except ValueError:
                        continue
                    add_edge(edge_type, e.from_node_id, e.to_node_id, "business_operating_ontology", {**dict(e.attributes), "source_fact_ids": list(e.source_fact_ids)})

        process_count = capability_count = action_count = provider_count = workflow_count = 0

        # Existing long-running process definitions are authoritative process contracts.
        for definition in sorted(process_definitions, key=lambda x: (str(getattr(x, "process_id", "")), str(getattr(x, "version", "")))):
            if getattr(definition, "workspace_id", None) != workspace_id:
                warnings.append(f"process_workspace_mismatch:{getattr(definition, 'process_id', 'unknown')}")
                continue
            process_id = _id("process", workspace_id, definition.process_id, definition.version)
            add_node(GraphNode(process_id, GraphNodeType.PROCESS, workspace_id, definition.name, "business_process_orchestration", {"process_id": definition.process_id, "version": definition.version, "initial_state": getattr(definition.initial_state, "value", str(definition.initial_state))}))
            process_count += 1
            for transition in definition.transitions:
                explicit_caps = _metadata_value(transition.metadata, "capability", "capabilities", "requires_capability")
                explicit_actions = _metadata_value(transition.metadata, "action", "action_type", "actions")
                explicit_providers = _metadata_value(transition.metadata, "provider", "providers")
                for cap in explicit_caps:
                    cap_id = _id("capability", workspace_id, cap)
                    add_node(GraphNode(cap_id, GraphNodeType.CAPABILITY, workspace_id, cap, "process_metadata"))
                    add_edge(GraphEdgeType.ENABLES, process_id, cap_id, "business_process_orchestration")
                for action in explicit_actions:
                    action_id = _id("action", workspace_id, action)
                    add_node(GraphNode(action_id, GraphNodeType.ACTION, workspace_id, action, "process_metadata"))
                    add_edge(GraphEdgeType.REALIZED_BY_ACTION, process_id, action_id, "business_process_orchestration")
                for provider in explicit_providers:
                    provider_id = _id("provider", workspace_id, provider)
                    add_node(GraphNode(provider_id, GraphNodeType.PROVIDER, workspace_id, provider, "process_metadata"))
                    add_edge(GraphEdgeType.PROVIDED_BY, process_id, provider_id, "business_process_orchestration")

        # Existing bounded capability registry is projected, never copied into a new registry.
        for spec in sorted(capability_specs, key=lambda x: (str(getattr(x, "name", "")), str(getattr(x, "version", "")))):
            if getattr(spec, "workspace_id", None) != workspace_id:
                warnings.append(f"capability_workspace_mismatch:{getattr(spec, 'name', 'unknown')}")
                continue
            cap_id = _id("capability", workspace_id, spec.name)
            add_node(GraphNode(cap_id, GraphNodeType.CAPABILITY, workspace_id, spec.name, "capability_registry", {"version": spec.version, "risk": getattr(spec.risk, "value", str(spec.risk)), "authority": getattr(spec.authority, "value", str(spec.authority)), "status": getattr(spec.status, "value", str(spec.status)), "reversible": spec.reversible}))
            capability_count += 1
            for prerequisite in spec.prerequisites:
                dep_id = _id("capability", workspace_id, prerequisite)
                add_node(GraphNode(dep_id, GraphNodeType.CAPABILITY, workspace_id, prerequisite, "capability_registry_reference"))
                add_edge(GraphEdgeType.DEPENDS_ON, cap_id, dep_id, "capability_registry")

        # Sector Platform catalog: explicit pack ownership and declared workflows/actions.
        for pack in sorted((sector_platform or {}).get("packs", []), key=lambda x: str(x.get("pack_id", ""))):
            pack_id = str(pack.get("pack_id", ""))
            if not pack_id:
                continue
            sector_id = _id("sector", workspace_id, pack_id)
            add_node(GraphNode(sector_id, GraphNodeType.SECTOR, workspace_id, str(pack.get("display_name", pack_id)), "sector_platform_read_model", {"pack_id": pack_id, "version": pack.get("version")}))
            for workflow in sorted(set(str(x) for x in pack.get("workflows", []) if str(x))):
                wf_id = _id("workflow", workspace_id, pack_id, workflow)
                add_node(GraphNode(wf_id, GraphNodeType.WORKFLOW, workspace_id, workflow, "sector_platform_read_model", {"pack_id": pack_id}))
                add_edge(GraphEdgeType.CONTAINS_WORKFLOW, sector_id, wf_id, "sector_platform_read_model")
                workflow_count += 1
            for capability in sorted(set(str(x) for x in pack.get("capabilities", []) if str(x))):
                cap_id = _id("capability", workspace_id, capability)
                add_node(GraphNode(cap_id, GraphNodeType.CAPABILITY, workspace_id, capability, "sector_platform_read_model", {"pack_id": pack_id}))
                add_edge(GraphEdgeType.OFFERS_CAPABILITY, sector_id, cap_id, "sector_platform_read_model")
            for action_record in sorted(pack.get("external_actions", []), key=lambda x: str(x.get("action", ""))):
                action = str(action_record.get("action", ""))
                if not action:
                    continue
                action_id = _id("action", workspace_id, action)
                contract = action_record.get("contract") or {}
                add_node(GraphNode(action_id, GraphNodeType.ACTION, workspace_id, action, "sector_platform_read_model", {"pack_id": pack_id, "execution_binding": action_record.get("execution_binding"), "risk": contract.get("risk_level") if isinstance(contract, Mapping) else None}))
                add_edge(GraphEdgeType.PART_OF, action_id, sector_id, "sector_platform_read_model")
                action_count += 1
                # Deliberately no capability edge here: sector manifests do not declare that mapping.
                for provider in _metadata_value(contract if isinstance(contract, Mapping) else {}, "provider", "providers"):
                    provider_id = _id("provider", workspace_id, provider)
                    add_node(GraphNode(provider_id, GraphNodeType.PROVIDER, workspace_id, provider, "action_contract_metadata"))
                    add_edge(GraphEdgeType.PROVIDED_BY, action_id, provider_id, "action_contract_metadata")

        # Connector Fabric provider/action relationships are authoritative when supplied.
        for record in sorted((connector_fabric or {}).get("connectors", []), key=lambda x: str(x.get("connectorKey", x.get("provider", "")))):
            provider = str(record.get("provider", record.get("connectorKey", "")))
            if not provider:
                continue
            provider_id = _id("provider", workspace_id, provider)
            add_node(GraphNode(provider_id, GraphNodeType.PROVIDER, workspace_id, provider, "connector_fabric_read_model", {"connector_key": record.get("connectorKey"), "certification": record.get("certification"), "health": record.get("health")}))
            provider_count += 1
            for contract in sorted(record.get("actionContracts", []), key=lambda x: str(x.get("action", ""))):
                action = str(contract.get("action", ""))
                if not action:
                    continue
                action_id = _id("action", workspace_id, action)
                add_node(GraphNode(action_id, GraphNodeType.ACTION, workspace_id, action, "connector_fabric_read_model"))
                add_edge(GraphEdgeType.PROVIDED_BY, action_id, provider_id, "connector_fabric_read_model")

        coverage = {
            "processes": process_count,
            "capabilities": capability_count,
            "actions": action_count,
            "providers": provider_count,
            "workflows": workflow_count,
            "nodes": len(nodes),
            "edges": len(edges),
        }
        if not nodes:
            warnings.append("empty_graph")

        return BusinessProcessCapabilityGraph(
            schema=SCHEMA,
            workspace_id=workspace_id,
            nodes=tuple(sorted(nodes.values(), key=lambda n: (n.node_type.value, n.name, n.node_id))),
            edges=tuple(sorted(edges.values(), key=lambda e: (e.edge_type.value, e.source_node_id, e.target_node_id, e.edge_id))),
            warnings=tuple(sorted(set(warnings))),
            coverage=coverage,
        )


__all__ = ["BusinessProcessCapabilityGraphProjector", "SCHEMA"]
