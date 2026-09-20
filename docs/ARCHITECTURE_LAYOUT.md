# Kanyoza directory layout (Phase B)

## Frozen ownership

| Package | Owns |
|---------|------|
| `species/` | Auth, authorization, laws, platform Guardian, events, global config, runtime, observability |
| `organism/` | Per-workspace mind: memory, metabolism, council view, director, pulse, dreams, registry |
| `domain/` | content, publishing, automation, workflows, analytics, knowledge |
| `plugins/social/` | Social network connectors |
| `plugins/industries/` | Industry organs |
| `plugins/integrations/` | GitHub, Growthbridge, backup, … |
| `council/` | Canonical Council implementation |
| `api/v1/` | Modular HTTP surface |

## Import guidance (new code)

```python
from species.auth import species_auth, AuthIdentity
from organism import Organism, OrganismRegistry
from organism.council import CouncilOrchestrator
from organism.metabolism import ...
from domain.publishing import ...
```

Implementations may still live under `core/` until incremental physical moves complete.
Facades define the architectural boundary now.
