# Trend Intelligence — no LLM by default

Trend scoring and decisions are **deterministic** (keywords, engagement heuristics, rules).

## LLM is OFF unless you opt in

| Variable | Effect |
|----------|--------|
| *(unset)* | **No LLM** |
| `TREND_USE_LLM=1` | Allow LLM features |
| `TREND_SEMANTIC_SCORE=1` | + needs `TREND_USE_LLM=1` |
| `TREND_LLM_BRIEFING=1` | + needs `TREND_USE_LLM=1` |
| `TREND_DETERMINISTIC=1` | Force no LLM always |
| `TREND_USE_LLM=0` | Force no LLM |

## Stop Gemini rate limits from Trend

On Render, **remove or set to 0**:
- `TREND_USE_LLM`
- `TREND_SEMANTIC_SCORE`
- `TREND_LLM_BRIEFING`

If Gemini still rate-limits, the caller is likely **Content Engine / chat / tool agent**, not Trend.

## Reduce Supabase hammering (optional)

`TREND_SCAN_INTERVAL_SECONDS=900` (or 1800) on free tier.
