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
