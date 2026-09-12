# Sprint 4 — SLOs + isolation contracts

## SLO endpoints

| Method | Path | Who |
|--------|------|-----|
| GET | `/api/v1/slo/workspace/{workspace_id}` | tenant or species |
| GET | `/api/v1/slo/platform` | species only |
| GET | `/api/v1/slo/health?workspace_id=` | compact probe |

Also embedded under `/api/v1/organism/vitals` → `slo` field.

## Targets (defaults)

- publish success rate ≥ 95% (when outcomes exist)
- outbox pending ≤ 50
- outbox dead ≤ 0
- bus dead letters ≤ 10

## Job isolation

```python
job = await job_service.find_by_id_for_workspace(job_id, workspace_id)
# returns None if job belongs to another tenant
```

## Tests

```bash
pytest tests/test_sprint4_slo_isolation.py -v
```
