# Architecture Convergence Status

## Publish path (canonical)

```
API POST /posts (publish)
  → gate_publish / run_action_pipeline
  → DecisionGate.issue (singleton)
  → AutomationJob.payload.decision_id
  → publish_job_task / outbox process_item
  → assert_publish_allowed (strict in prod)
  → run_publish → connector
```

## Implemented

| Component | Path |
|-----------|------|
| Authorization helper | `core/publish_authorization.py` |
| Orchestration | `services/publish_orchestration.py` |
| Worker | `services/publish_worker.py` |
| Outbox | `core/publish_outbox.py` (passes decision_id) |
| Singleton gate | `core.decision_gate.decision_gate` |

## Tests

**23 passed** including strict DecisionGate + pipeline→gate acceptance.

## Remaining

- Real social connectors inside `run_publish` (not simulation id)
- Data migration off `api_keys`
- Provider-accurate AI token metering
- Full e2e clean CI
