# KDO Phase 9.2 — Correlated Metrics & Distributed Trace Context

## Status
IMPLEMENTED — contract/integrity scope.

## Purpose
Provide one authority-free correlation identity that lets KDO connect operational metrics and distributed trace observations to the existing ActionIntent, execution, provider, receipt, verification, reconciliation, and incident evidence.

This is **not** a new execution ledger, event store, authorization system, or workflow engine.

## Canonical correlation dimensions

Every correlated observation may carry:

- `workspace_id` — mandatory backend-derived scope
- `correlation_id` — stable investigation/correlation identity
- `trace_id` — distributed trace root identity
- `span_id` — individual observation/span identity
- `causation_id` — immediate causal predecessor where known
- `execution_id` — existing canonical execution identity when applicable
- `incident_id` — incident identity when applicable
- `dependency_id` — provider/dependency identity when applicable
- `evidence_ref` — pointer to authoritative evidence when applicable

The context has `authority=none` by construction.

## Propagation model

```text
ActionIntent / existing execution identity
                │
                ▼
       Correlation Context
                │
        ┌───────┴────────┐
        ▼                ▼
   Trace span       Metric observation
        │                │
        ├───────┬────────┤
        ▼       ▼        ▼
     Connector Provider Verification
        │       │        │
        └───────┼────────┘
                ▼
        Reconciliation / Incident
```

Child spans preserve `correlation_id` and `trace_id` while receiving a new `span_id` and a default `causation_id` pointing to the parent span.

## Security boundary

Telemetry must not become a credential side channel. Metric and trace dimensions reject obvious credential/authorization fields such as:

- token
- secret
- password
- credential
- authorization

Sensitive material belongs in the Phase 8 credential/security boundary and must never be copied into operational dimensions.

## State/authority boundary

Correlation can explain an action; it cannot authorize or execute one.

```text
Telemetry evidence
       │
       ▼
Operational understanding
       │
       ▼
Governance / Authorization
       │
       ▼
Action Kernel
```

No Phase 9 telemetry object may grant authority.

## Validation

- Phase 9.1 + 9.2 tests: **14/14 PASS**
- Python `compileall`: **PASS**

## Not claimed

This contract does not yet claim production OpenTelemetry/Prometheus/collector deployment, distributed storage, retention policy, live sampling, or backend-wide propagation. Those are implementation/infrastructure concerns to be integrated without changing this canonical correlation contract.

## Next phase

Phase 9.3 — Incident & causal graph model: turn correlated evidence into authoritative incident relationships and causal investigation paths without making the observability layer an execution authority.
