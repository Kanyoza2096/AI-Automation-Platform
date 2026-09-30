# Hotfix — Gemini primary + LLM caller policy

## Provider order
Gemini → DeepSeek → Groq → Mistral

## Default LLM callers allowed
`content`, `content_engine`, `director`

## Blocked by default
`meta`, `council`, `trend`, `news`, `tool_agent`, `unknown`

## Env
```
LLM_ALLOWED_CALLERS=content,director,content_engine   # default if unset
META_LLM_NARRATIVE=0
COUNCIL_LLM_SUMMARY=0
TREND_LLM_BRIEFING=0
LLM_PRIMARY_PROVIDER=gemini
```

To re-enable Meta narrative later:
`LLM_ALLOWED_CALLERS=content,director,meta` and `META_LLM_NARRATIVE=1`
