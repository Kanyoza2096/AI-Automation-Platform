# Sprint 5 — Production compose, SLO webhooks, weekly reports

## Processes

| Mode | Command |
|------|---------|
| api | `SERVICE_MODE=api uvicorn main:app --host 0.0.0.0 --port 8000` |
| core | `SERVICE_MODE=core python -m workers.brain` |
| workers | `SERVICE_MODE=workers python -m workers.scheduler_worker` |

Compose: `deploy/docker-compose.production.yml`

## SLO webhook

```bash
export SLO_ALERT_WEBHOOK_URL=https://hooks.slack.com/services/...
export SLO_ALERT_COOLDOWN_SECONDS=300
export SLO_ALERT_ENABLED=true
```

Monitor runs every 60s inside core/full boot.

Force check:

```bash
curl -X POST "$HOST/api/v1/slo/alert-check?workspace_id=default" \
  -H "Authorization: Bearer $TOKEN"
```

## Weekly organism report

```bash
curl "$HOST/api/v1/reports/workspace/default?window_days=7" \
  -H "Authorization: Bearer $TOKEN"

# Species: all workspaces
curl -X POST "$HOST/api/v1/reports/run-weekly" \
  -H "Authorization: Bearer $MASTER_API_TOKEN"
```

Report includes: narrative, SLO block, outcomes, dream insights, organism stats.

## Tests

```bash
pytest tests/test_sprint5_alerts_reports.py -v
```
