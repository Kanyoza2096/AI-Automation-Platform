# Kanyoza Canonical Architecture

**Status:** Governing document for production convergence  
**Principle:** Do not add another architectural concept until existing concepts have one canonical implementation and a formally defined responsibility.

## Hierarchy

```
KANYOZA SPECIES
  ├── Global Law, Security, Infrastructure
  └── ORGANISM REGISTRY → Tenant Organisms
        ├── Council · Director · Memory · Metabolism · Guardian · Pulse · Dreams
        └── Domain Engines → Plugin Layer → External World → Events → Memory
```

## Non-negotiable laws

1. **One responsibility, one canonical implementation** — no Council A/B/Legacy.
2. **Workspace never comes from the client alone** — only from authenticated context (Species operators may select).
3. **Production fails closed** — no silent in-memory persistence when infrastructure is down.
4. **External side effects go through Outbox/Jobs** — not direct platform calls from schedulers.
5. **Every request has RequestContext** — principal, workspace, role, permissions, species privileges.

## Request flow

```
HTTP → Auth → RequestContext → Authorization → Guardian → Organism
  → Memory / Metabolism / Council → Director → Decision
  → Domain Service → Job/Outbox → Worker → Plugin → Platform
  → Event → Analytics / Memory → Observability
```

## Definition of done

Every request has authenticated identity and workspace context; every tenant operation is tenant-scoped; every autonomous action passes organism policy, guardian, metabolism and authority controls; every external side effect is durable through job/outbox; every important state transition is observable and auditable; every major subsystem has exactly one canonical implementation; production failures fail explicitly; the system rebuilds from a clean environment.

## Canonical platform identity

The KDO architecture is best understood top-down: the **Core is the invariant**;
sector packs are specialization that changes when KDO enters a new operational
domain.

```text
                          KDO CORE
 ┌───────────────────────────────────────────────────────┐
 │ Intelligence                                          │
 │ Decision / Council / Director                         │
 │ Governance / Authorization                            │
 │ Workflow / Orchestration                              │
 │ Canonical Action Kernel                               │
 │ Durable Execution + Idempotency                       │
 │ Receipt Propagation Invariant                          │
 │ Ledger + Outcome Semantics                             │
 │ Verification + Reconciliation                          │
 │ Security + Tenancy                                    │
 └─────────────────────────┬─────────────────────────────┘
                           │
                ╔══════════▼══════════╗
                ║  CONNECTOR FABRIC   ║
                ║  Receipt Protocol   ║  sector-neutral
                ║  Action Contracts   ║  governed/domain
                ║  Provider Adapters  ║  vendor-specific
                ╚══════════╤══════════╝
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
  Marketing Pack      IT/Ops Pack       Commerce Pack
        │                  │                  │
        ▼                  ▼                  ▼
  Healthcare          Finance           Manufacturing

 ═══════════════════════════════════════════════════════════
  OBSERVABILITY + MISSION CONTROL (cross-cutting)
  logs · metrics · traces · health · incidents · audit
 ═══════════════════════════════════════════════════════════
```

### Core guarantees

The Core is the architectural identity of KDO. Adopting the Core means adopting
these guarantees rather than rebuilding them for every sector:

- intelligence and deliberation
- governance and authorization
- workflow/orchestration
- one canonical execution boundary
- durable execution and idempotency
- receipt propagation
- durable outcome semantics
- verification and reconciliation
- security and tenancy
- cross-cutting observability and operator control

### Receipt Propagation Invariant

External execution is governed by a sector-neutral receipt protocol. Once a
provider produces an authoritative receipt, downstream KDO layers may
normalize its representation but must not silently discard its identity,
status, or provenance.

```text
Provider Receipt
      ↓
Canonical Receipt
      ↓
Canonical Outcome
      ↓
Execution Ledger
      ↓
Workflow / Domain State
      ↓
Operational Projection / Mission Control
```

A provider success without the object identity required to prove the external
side effect is classified as **uncertain**, not success. An uncertain result
enters reconciliation rather than blind retry.

### Connector Fabric

The fabric has three distinct responsibilities:

1. **Receipt Protocol** — sector-neutral provider evidence.
2. **Action Contracts** — actions declared by sector/domain packs and governed
   by the KDO authorization/execution substrate.
3. **Provider Adapters** — vendor-specific external API integration.

This separation prevents connector implementations from becoming a second
policy engine or execution authority.

### Observability is cross-cutting

Observability is not a lower execution layer. Every Core component, connector,
and sector pack emits telemetry/evidence into the same operational plane.
Mission Control consumes these projections for human/operator visibility.

### Organism view

The engineering architecture has a corresponding biological interpretation:

| Engineering concept | Organism interpretation |
|---|---|
| KDO Core | Shared cellular machinery |
| Tenant | Autonomous organism instance/boundary |
| Intelligence | Cognition |
| Council | Deliberative nervous system |
| World model / perception | Sensory representation |
| Ledger | Durable memory |
| Action Kernel | Controlled motor/execution system |
| Connector Fabric | Senses and limbs reaching the external world |
| Sector Pack | Specialized organ system |
| Verification | Reality checking |
| Reconciliation | Homeostasis |
| Security / Tenancy | Boundary / membrane |
| Mission Control | Self-observation and operator consciousness |
| Learning | Adaptation |

The organism metaphor is a second view of the same architecture; it does not
create a second runtime architecture or execution authority.
