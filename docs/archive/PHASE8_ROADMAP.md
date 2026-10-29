# KDO Phase 8 — Security, Resilience & Operational Control

## Objective
Make KDO trustworthy under attack, failure, degraded dependencies, restart,
reconciliation and operational stress without moving authority into observability
or security projections.

## Exit domains
1. Security control plane
2. Tenant/workspace isolation hardening
3. Identity/access hardening
4. Authorization hardening
5. Secrets/credential governance
6. Threat/anomaly detection
7. Security incident control
8. Runtime observability
9. Failure/degradation management
10. Durable recovery
11. Reconciliation/drift control
12. Resilience testing
13. Disaster recovery
14. Security/resilience certification

## Authority invariant
Security, observability, recovery and certification layers are evidence/control
surfaces. They do not become an alternative execution authority. Consequential
changes continue through Governance -> Authorization -> Action Kernel -> Connector
Fabric -> Provider -> Receipt -> Verification -> Reconciliation.

## 8.1 exit criterion
A workspace-scoped security posture can be durably recorded, evaluated and
rehydrated; degraded/unknown/quarantined controls cannot be represented as
healthy; records are tamper-evident; and the security control surface cannot
authorize, execute, call providers or invoke the Action Kernel.


## 8.2 exit criterion
A reusable tenant/workspace isolation boundary durably binds resources to one exact scope; cross-tenant, cross-workspace, unknown-resource, rebinding and tampered-scope access fail closed; scope survives restart; and the isolation layer cannot authorize, execute, call providers or invoke the Action Kernel.
