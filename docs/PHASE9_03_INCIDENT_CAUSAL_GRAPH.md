# KDO Phase 9.3 — Incident & Causal Graph

## Purpose

Phase 9.3 turns correlated operational evidence into durable incident cases and explicit evidence-backed graph relationships.

The observability layer remains an authority-free operational intelligence layer.

## Canonical boundary

Operational Event / Metric / Trace / Receipt / Verification / Reconciliation
→ Incident
→ Evidence-backed relationship graph
→ Investigation / impact projection
→ governed response through the existing ActionIntent boundary

The incident system MUST NOT execute providers, authorize actions, mutate business state, or replace the Action Kernel.

## Core rules

1. Every incident is workspace-scoped.
2. Every causal edge is workspace-scoped.
3. Incidents and edges carry `authority="none"`.
4. Incidents are immutable records; status changes create new values rather than mutating the existing value.
5. Causal edges require correlation and evidence references.
6. Self-causation is rejected.
7. Cross-workspace graph relationships are rejected.
8. Correlation does not imply causation.
9. Temporal proximity alone does not create a causal edge.
10. Impact traversal is read-only and cannot issue remediation.

## Incident lifecycle

OPEN → INVESTIGATING → CONTAINED → RECOVERING → RESOLVED → CLOSED

The implementation also permits carefully bounded recovery paths back to investigation when new evidence invalidates the previous operational state.

## Relationship types

- CAUSED_BY
- TRIGGERED_BY
- DEPENDS_ON
- BLOCKED_BY
- AFFECTS
- CORRELATED_WITH
- RECOVERED_BY
- RECONCILED_BY

These are evidence relationships, not execution commands.

## Example

Provider outage
→ connector degradation
→ affected execution
→ verification uncertainty
→ reconciliation required
→ incident

A later recovery operation may be linked with `RECOVERED_BY`, but the graph itself never authorizes or invokes that recovery.

## Non-goals

This phase does not implement:
- live incident storage
- distributed tracing collectors
- Prometheus/OpenTelemetry deployment
- automatic root-cause inference
- remediation execution
- disaster recovery
- production RLS certification

Those belong to later integration and certification work.
