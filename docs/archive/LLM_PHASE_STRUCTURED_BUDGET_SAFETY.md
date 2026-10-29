# LLM phase — structured output, budget, safety

## `complete_structured(...)`
- Asks for JSON; parses with `parse_structured_json`
- Parse failure or missing `required_keys` → `ok=False`, `mode=degraded`
- Never invents schema fields

## Budget
- Key: `llm:budget:{date}:{workspace_id}`
- Redis when `REDIS_URL` / client available; else memory
- TTL 48h on Redis keys
- Exhaustion → degraded (existing path)

## Safety
- `sanitize_prompt`: redacts email/phone patterns; bounds length
- Injection markers → untrusted-content prefix (does not execute attacker instructions as system policy)

## Health
- `daily_token_budget`, `platform_tokens_used_approx`, `budget_backend`: redis|memory

## Meta
- Optional narrative prefers structured `{summary, risk}` when enabled
