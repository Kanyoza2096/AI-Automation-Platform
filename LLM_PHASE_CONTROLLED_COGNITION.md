# LLM phase — controlled cognition (report §5–6, §36–37)

## Rule
LLMs enhance. Deterministic paths + policy remain authoritative.

## Gateway
- Boot step: **LLMGateway** (before AIOrchestrator)
- Module: `ai/gateway.py` — modes full | degraded | unavailable

## Wired call sites
| Component | Path | Degraded behavior |
|-----------|------|-------------------|
| Content | already | deterministic caption |
| Tool agent | already | clear unavailable message |
| **Trend** `_semantic_relevance` | gateway | returns None → heuristic score only |
| **Trend** `_brief_for_social` | gateway | returns None → non-LLM post text |
| **Meta** diagnosis | optional `META_LLM_NARRATIVE=1` | narrative only; root cause stays deterministic |
| **Council** | optional `COUNCIL_LLM_SUMMARY=1` via `council/intelligence/llm_enrichment.py` | default remains **LLM-free** |

## Env flags
- `TREND_SEMANTIC_SCORE=1`
- `TREND_LLM_BRIEFING=1`
- `META_LLM_NARRATIVE=1`
- `COUNCIL_LLM_SUMMARY=1`
- `LLM_GATEWAY_DISABLED=1` / budgets / circuit (see LLM_GATEWAY.md)

## Still later
- Structured JSON schema enforcement platform-wide
- Persisted workspace token budgets
- PII / injection boundaries
