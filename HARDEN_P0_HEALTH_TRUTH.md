# HARDEN P0 — Health truthfulness (report §27)

## Distinctions
| Endpoint | Meaning |
|----------|---------|
| `/health`, `/health/live` | **Liveness** — process alive |
| `/health/ready` | **Readiness** — can perform required functions |
| `/health/detailed` | **Health** — how well (may be degraded) |

## Rules applied
- LLM degraded/unavailable does **not** make readiness fail (enhancement only)
- Missing Redis → coordination `degraded_local` → detailed may show **degraded**
- Ready response may be `ready_degraded` when intel/coordination limited
- Detailed includes `intelligence`, `coordination`, `director_unbound_capabilities`, `dimensions`

## Related
Director placeholders no longer report success (HARDEN_P0_DIRECTOR_TRUTH.md).
