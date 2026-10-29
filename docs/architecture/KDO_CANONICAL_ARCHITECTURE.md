# KDO Canonical Architecture

**Status:** Governing architecture specification
**Scope:** Kanyoza Digital Organism (KDO) core, connector fabric, sector packs, external providers, observability, and operator control.

## 1. Architectural claim

KDO is a governed autonomous execution platform whose invariant machinery survives the introduction of a new sector. Sector packs specialize domain vocabulary and workflows; they do not create independent execution, governance, receipt, tenancy, or reconciliation authorities.

> **The KDO Core survives contact with a new sector; the sector pack is what changes.**

## 2. Canonical engineering view

```text
                              OPERATORS
                                  |
                                  | Mission Control / command
                                  v
                       +-----------------------+
                       |       KDO CORE        |
                       |-----------------------|
                       | Intelligence           |
                       | Decision / Council /   |
                       | Director               |
                       | Governance /           |
                       | Authorization          |
                       | Workflow /             |
                       | Orchestration           |
                       | Canonical Action Kernel |
                       | Durable Execution +     |
                       | Idempotency             |
                       | Receipt Propagation     |
                       | Invariant               |
                       | Ledger + Outcome        |
                       | Semantics               |
                       | Verification +          |
                       | Reconciliation          |
                       | Security + Tenancy      |
                       +-----------+-------------+
                                   |
                        +==========v==========+
                        || CONNECTOR FABRIC  ||
                        ||-------------------||
                        || Receipt Protocol  ||  sector-neutral
                        || Action Contracts  ||  governed/domain
                        || Provider Adapters ||  vendor-specific
                        +==========+==========+
                                   |
               +-------------------+-------------------+
               |                   |                   |
               v                   v                   v
        Marketing Pack       Commerce Pack       IT/Ops Pack
               |                   |                   |
               v                   v                   v
        Social / Media      Commerce / Payment   Infrastructure
          Providers              Providers          Providers

        Healthcare Pack      Finance Pack       Manufacturing Pack
               |                   |                   |
               v                   v                   v
          EHR Systems        Banking Rails        MES / ERP

                         EXTERNAL WORLD

===============================================================
 OBSERVABILITY + MISSION CONTROL — CROSS-CUTTING
 logs · metrics · traces · health · incidents · audit · evidence
===============================================================
                                  |
                                  v
                              OPERATORS
```

The diagram is a logical architecture, not a literal process stack. The Core owns invariant semantics; the Fabric is the controlled external boundary; packs own domain specialization; providers represent external reality.

## 3. Core guarantees

The Core is the identity statement of KDO:

1. **Intelligence** — reasoning, world interpretation, prediction, and learning interfaces.
2. **Decision / Council / Director** — deliberation, orchestration, and supervisory control.
3. **Governance / Authorization** — policy, authority, capability, tenant, and risk gates.
4. **Workflow / Orchestration** — durable coordination without creating a second execution authority.
5. **Canonical Action Kernel** — the single canonical consequential execution boundary.
6. **Durable Execution + Idempotency** — execution records, leases/fencing, replay protection, and recovery semantics.
7. **Receipt Propagation Invariant** — authoritative external evidence cannot be silently lost, contradicted, or upgraded from uncertainty.
8. **Ledger + Outcome Semantics** — durable record of execution and canonical outcome classification.
9. **Verification + Reconciliation** — external truth is checked and ambiguous outcomes are resolved without unsafe re-execution.
10. **Security + Tenancy** — workspace-scoped authority, isolation, credentials, and policy boundaries.

## 4. Receipt Propagation Invariant

The Receipt Propagation Invariant (RPI) is a governing correctness property of canonical external execution.

```text
Provider Receipt
      |
      v
Canonical Receipt
      |
      v
Canonical Outcome
      |
      v
Execution Ledger
      |
      v
Workflow / Domain State
      |
      v
Operational Projection / Mission Control
```

Once a provider produces an authoritative receipt, downstream layers may normalize representation but must preserve its identity, status, provenance, and relevant evidence.

### Evidence semantics

```text
Intent
  -> Authorized Action
  -> Execution
  -> Provider Receipt
  -> Canonical Outcome
  -> Verification
  -> Business Outcome
```

A provider receipt is evidence of an external effect; it is not automatically proof that the higher-level business objective succeeded.

### Failure rules

```text
provider success + authoritative ID  -> succeeded
provider success + missing ID        -> uncertain
transport ambiguity                  -> uncertain
provider definitive rejection        -> failed
uncertain                            -> reconciliation
```

Uncertainty cannot silently become success.

## 5. Connector Fabric

The Connector Fabric has three explicit concerns:

### 5.1 Receipt Protocol

A sector-neutral contract that normalizes provider evidence into `ProviderReceipt`. Vendor adapters may use provider-specific terminology at their boundary; the canonical execution plane does not.

### 5.2 Action Contracts

Sector/domain packs declare governed external actions. KDO governance and the Action Kernel remain authoritative for authorization and execution. The Fabric must never become a second policy or execution engine.

### 5.3 Provider Adapters

Vendor-specific integration code translates canonical action requests into external API calls and translates responses into canonical receipt evidence.

## 6. Sector packs

Sector packs contain domain specialization:

- domain resources and repositories;
- domain commands/use cases;
- sector workflows;
- sector policies;
- sector events/webhooks;
- declared external actions;
- domain outcome semantics.

They do **not** own independent:

- execution ledgers;
- authorization engines;
- tenant isolation systems;
- provider receipt protocols;
- reconciliation engines;
- external execution authorities.

The current portfolio contains 13 sector packs, each with a v1.1 operational layer.

## 7. External-world separation

Each sector reaches a distinct external vocabulary through the shared Fabric:

| Sector | Example external world |
|---|---|
| Marketing & Media | social/media providers |
| Commerce | commerce/payment providers |
| IT Operations | infrastructure/cloud providers |
| Cybersecurity | security platforms |
| CRM | communications/CRM providers |
| Education | education/communication systems |
| Healthcare Administration | administrative healthcare systems |
| Finance | banking/payment rails |
| Supply Chain | carrier/warehouse systems |
| Manufacturing | MES/ERP/industrial systems |
| Energy & Utilities | utility/field systems |
| Government | public-service/field systems |
| Agriculture | farm/field/input systems |

The Fabric is shared; the external worlds are not.

## 8. Observability and Mission Control

Observability is cross-cutting, not a sequential layer. Every authoritative subsystem emits logs, metrics, traces, health signals, incidents, audit records, and evidence according to its contract.

Mission Control consumes these projections and provides the operator-facing command and observation surface. Mission Control does not become an alternative source of execution truth.

The control loop is therefore:

```text
Operator intent
      -> KDO governance and execution
      -> external world
      -> receipt / observation
      -> verification / reconciliation
      -> observability / Mission Control
      -> operator
```

## 9. Organism view

The engineering view and organism view describe the same system.

| Engineering concept | Organism interpretation |
|---|---|
| KDO Core | shared organism machinery |
| Tenant organism | autonomous organism instance on shared machinery |
| Intelligence | cognition |
| Council | deliberative nervous system |
| World Model | internal environmental representation |
| Memory | durable memory |
| Action Kernel | controlled motor/execution system |
| Connector Fabric | senses and limbs into the external world |
| Ledger | durable execution memory |
| Verification | reality checking |
| Reconciliation | homeostasis |
| Security/Tenancy | boundary/membrane |
| Sector Packs | specialized organ systems |
| Mission Control | self-observation and operator consciousness |
| Learning | adaptation |

The metaphor is explanatory, not an excuse to create duplicate runtime subsystems.

## 10. Authority rules

1. Durable execution state is authoritative in the execution ledger/database boundary.
2. Internal event delivery is not proof of external success.
3. External providers are authoritative for provider-side receipts, not KDO internal lifecycle state.
4. The Action Kernel is the sole canonical consequential execution authority.
5. Sector packs may declare actions but cannot bypass governance or durable execution.
6. Ambiguous external outcomes remain uncertain until reconciled.
7. Observability can report state but cannot grant authority.
8. Autonomous cognition cannot grant itself credentials, privileges, budgets, or execution authority.
9. Simulation/dreaming cannot directly mutate production state.
10. Human/operator controls retain the ability to stop governed autonomous execution.

## 11. Portability claim

A new sector should primarily require:

```text
Domain model
+ Domain workflows
+ Domain policies
+ Sector action declarations
+ Sector events/webhooks
+ Provider adapters
```

It should reuse:

```text
Intelligence
Governance
Action Kernel
Execution Ledger
Idempotency
Receipt Protocol
Verification
Reconciliation
Security/Tenancy
Observability
Mission Control
```

That is the architectural basis for sector portability.

## 12. Paper thesis

The architecture paper should center on one problem: autonomous systems can successfully cause an external effect while internally reporting the wrong truth about that effect.

The central claim is that external execution correctness requires a named, testable receipt propagation invariant rather than an implicit assumption that success metadata will survive across layers.

Suggested paper structure:

1. **The Problem — Intent vs External Reality**
2. **Failure Model — How Autonomous Systems Lose Truth**
3. **Architectural Claim — Receipt Propagation Invariant**
4. **KDO Architecture — Core / Fabric / Sector / External World**
5. **Execution Semantics — Kernel → Receipt → Ledger → Verification**
6. **Evidence — Controlled publishing incident and certification**
7. **Portability — Sector Packs and the Connector Contract**
8. **Implications — Governed Autonomous Systems**

The engineering architecture is the primary figure. The organism view is a secondary conceptual figure.

## 13. Production boundary

Architectural completion is distinct from production certification. The KDO design may be fully specified and locally certified while live PostgreSQL/RLS, Redis recovery, controlled provider execution, webhook reconciliation, organism lifecycle, and isolated backup/restore still require authoritative staging evidence.

No diagram, test suite, or local harness substitutes for those live proofs.

Provider catalog: `docs/architecture/CONNECTOR_FABRIC_PROVIDER_CATALOG.md`.
