# Phase C — Organism per workspace — COMPLETE (code)

Goal: multi-tenant means **multi-organism**. Two workspaces never share mind state.

## Checklist

| Item | Status | Location |
|------|--------|----------|
| `Organism` + `OrganismConfig` | Done | `core/organism.py` |
| `OrganismRegistry` census | Done | `core/organism.py` |
| Persona catalog by industry organ | Done | church → theologian, etc. |
| Plan → Director authority metabolism | Done | free=suggest, pro=auto_low, business=auto_medium |
| Lifecycle registers registry + births default | Done | `lifecycle.py` |
| Auth binds → get_or_create organism | Done | `workspace_context.bind_auth_identity` |
| Synapses use workspace Council | Done | `core/synapses.py` |
| Plan can auto-approve medium (business) | Done | synapses action path |
| Isolation tests | Done | `tests/test_phase_c_organism.py` |

## Model

```
Species (process)
  ├── Reflex law, Meta infra, UnifiedEventBus, SynapseMatrix
  └── OrganismRegistry
        ├── Organism(ws_a)  Council / Memory / Dreams / Pulse thresholds
        └── Organism(ws_b)  independent body
```

## API for integrators

```python
from core.organism import organism_registry, get_organism

org = organism_registry.get_or_create(
    "ws_church",
    plan="pro",
    industry_organs=["church_mis"],
    timezone="Africa/Blantyre",
)
await org.start()
print(org.vitals())
```

## Next: Phase D

Metabolism as billing — Stripe/usage tied to posts + thought budget; expose plan limits in settings/API.
