# Phase B — One Nerve, One Loop — COMPLETE (code)

Goal: close the organism loop so a single `correlation_id` can travel from sense → act → measure → learn.

## Checklist

| Item | Status | Location |
|------|--------|----------|
| Nerve envelope on publish | Done (Phase A) | `core/nerve.py`, `unified_bus.publish` |
| Decision gate | Done | `core/decision_gate.py` |
| Publish authorization helper | Done | `core/publish_authorization.py` |
| Synapse matrix (closed loop) | Done | `core/synapses.py` |
| Lifecycle wires synapses | Done | `lifecycle.py` after UnifiedEventBus |
| AutonomousLoop canonical events | Done | `core/autonomous_loop.py` routing + subscribe |
| Legacy EventBus marked bridge-only | Done | `core/event_bus.py` banner |
| `decision_id` on AutomationJob | Done (Phase A) | `models/automation_job.py` |
| Unit tests | Done | `tests/test_phase_b_synapses.py` |

## Loop (wired)

```
condition.changed → director.proposal → action.proposed
    → Reflex (block?) → Council / Director-auto → council.decided
    → (hands use decision_id) → post.published / job.*
    → pulse.cleared | pulse.stagnant → memory.stored
    → dream.insight (consumed for next briefing)
```

## Operator / integrator steps

1. Before any social publish in pipeline or plugins:

```python
from core.publish_authorization import assert_publish_allowed
assert_publish_allowed(job.decision_id, job.workspace_id)
```

2. Set `KANYOZA_STRICT_DECISIONS=true` in staging to enforce fail-closed early.
3. Prefer publishing canonical event names (`post.published`, not only `post_published`).
4. SharedEventBus remains for **cross-process** workers only; in-process organs use UnifiedEventBus.

## Next: Phase C

`Organism(workspace_id)` factory — Director, Council weights, Memory, Pulse, Dreams scoped per workspace (multi-organism).
