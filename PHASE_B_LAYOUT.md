# Phase B — Directory alignment

## Created / expanded
- `organism/` — registry, lifecycle, memory, metabolism, council, director, pulse, dreams
- `species/` — auth, authorization, guardian, events, configuration, runtime, observability (+ laws)
- `domain/` — content, publishing, automation, workflows, analytics, knowledge
- `plugins/social/` — social connectors boundary
- `plugins/integrations/` — non-industry integrations
- Removed `plugins/council/` if present

## Strategy
Facades re-export from existing `core/` / `council/` / `services/` so imports can migrate without a big-bang move.

## Next (Phase C)
Wire one canonical publish path through Organism intelligence loop into memory.
