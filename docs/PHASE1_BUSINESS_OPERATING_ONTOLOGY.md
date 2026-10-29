# Phase 1 — Business Operating Ontology

## Purpose

Turn KDO's existing Business Activation / Knowledge Plane into a canonical **read-model projection of the business as an operational system**.

This phase does **not** create another knowledge database. Confirmed Knowledge Plane facts remain authoritative. The ontology is a deterministic projection used by reasoning, discovery, process, decision and control-plane features.

## Current boundary

`Confirmed current facts -> BusinessOperatingOntologyProjector -> BusinessOperatingOntology read model`

The projector excludes:

- unconfirmed facts
- superseded facts
- facts from another workspace

It cannot:

- confirm knowledge
- create facts
- supersede facts
- authorize actions
- execute providers

## Initial object model

- Business
- Sector
- Service
- Capability
- Process
- Policy
- Customer Segment
- Location
- Payment Method
- Contact Channel
- Product
- System
- Provider
- Person Role
- Knowledge Fact

The current projector maps the existing fact vocabulary into these operational objects where semantics are already explicit. Unmapped facts remain represented as `knowledge_fact` nodes rather than being silently discarded.

## Why this is an upgrade, not a duplicate

KDO already has multiple domain registries and knowledge services. Phase 1 does not replace them. It provides one stable cross-domain read model that can later connect business objects to processes, capabilities, decisions, actions and outcomes.

## Next extensions

1. Add explicit sector/service/capability relationships from existing sector-pack manifests and capability registries.
2. Add process nodes and process dependencies from existing orchestration definitions.
3. Add action/provider relationships from existing Action Kernel / Connector Fabric contracts.
4. Add ontology versioning and coverage evaluation without mutating source facts.
5. Feed the ontology into Phase 2 Process & Capability Graph.
