# Sprint 2 — Outcome feedback + correlation timeline

## Loop

```
decision_id issued
  → publish / action
  → outcome.recorded (immediate + engagement later)
  → Decision.outcome filled
  → Council reliability nudged
  → Dreams collects outcomes overnight
```

## APIs

| Method | Path |
|--------|------|
| GET | `/api/v1/timeline/correlation/{correlation_id}` |
| GET | `/api/v1/timeline/decision/{decision_id}` |
| GET | `/api/v1/timeline/workspace/{workspace_id}/decisions` |
| POST | `/api/v1/timeline/outcome` |

## Example

```bash
# After e2e publish, follow the story
curl "$HOST/api/v1/timeline/correlation/$CORR" -H "Authorization: Bearer $TOKEN"

# Human marks outcome
curl -X POST "$HOST/api/v1/timeline/outcome" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"decision_id":"dec_...","outcome":"success","score":0.8,"workspace_id":"default"}'
```
