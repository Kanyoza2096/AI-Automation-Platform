# KDO Backend Control Plane — vNext Phase 1

## Basis
This phase uses the clean `AI-Automation-Platform-KDO-LAB (4)` archive as the canonical backend source.

## Objective
Establish the first backend-owned operator read contract for the platform's certified sector-pack portfolio without moving execution authority into the Command Center or sector plugins.

## Implemented

### 1. Sector Pack Portfolio API
Added read-only routes:

- `GET /api/v1/sector-packs`
- `GET /api/v1/sector-packs/{pack_id}`

The route is backed directly by the existing `IndustryPlugin.get_manifest()` contract. It exposes:

- pack identity/version
- display name
- capabilities
- resources
- policies
- workflows
- events
- webhooks
- declared external actions

The response explicitly states that sector-pack metadata is read-only and that consequential actions remain owned by Governance → Action Kernel → Connector Fabric.

### 2. Certified portfolio boundary
The endpoint exposes the canonical 13-pack v1.1 portfolio certified by `tests/architecture/test_sector_portfolio_v11.py`:

- marketing_media
- commerce
- it_operations
- cybersecurity
- crm
- education
- healthcare
- finance
- supply_chain
- manufacturing
- energy
- government
- agriculture

Legacy/special-purpose domain plugins such as School MIS, Hospital MIS, Church MIS and PDF Merger are not silently promoted into this enterprise portfolio.

### 3. Route wiring
The new router is mounted by `main.py` and included in the route-wiring certification set.
The canonical route manifest has been regenerated from the backend AST.

## Architectural boundary

```text
Sector Pack Manifest
        ↓
Capability / Resource / Policy / Workflow / Event declaration
        ↓
ActionIntent
        ↓
Governance
        ↓
Action Kernel
        ↓
Connector Fabric
        ↓
External Provider
```

The new API does **not** execute sector commands and does not grant authority.

## Validation

Targeted certification suite:

- sector portfolio certification
- sector/action cross-boundary certification
- connector certification
- connector activation registry
- connector health/drift control
- enterprise provider adapter certification
- Action Kernel tests
- receipt propagation invariant
- execution ledger reliability
- route wiring

Result: **74 passed**.

## Remaining production gates

This is source-level/backend test certification only. It does not prove live production provider execution, live database/RLS behavior, live credentials, staging reconciliation, or physical-device Command Center behavior.

Those remain deployment/staging certification gates.
