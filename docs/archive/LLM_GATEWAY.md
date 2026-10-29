# LLM Gateway — single intelligence spine

## Provider order (2026-08-28)
1. **Gemini** (primary)
2. DeepSeek (fallback)
3. Groq
4. Mistral

## Caller policy (default)
Only these callers may use the LLM:
- `content` / `content_engine` — post generation
- `director` — optional reasoning

Blocked by default: `meta`, `council`, `trend`, `news`, `tool_agent`, `eval`, `unknown`.

```bash
# default (recommended)
# LLM_ALLOWED_CALLERS unset → content,director,content_engine

# enable meta narrative later
LLM_ALLOWED_CALLERS=content,director,meta

# allow everything (not recommended)
LLM_ALLOWED_CALLERS=all
```

Also keep enrichment flags off unless needed:
```
META_LLM_NARRATIVE=0
COUNCIL_LLM_SUMMARY=0
TREND_LLM_BRIEFING=0
LLM_PRIMARY_PROVIDER=gemini
```

## API
```python
get_llm_gateway().complete(prompt, workspace_id=ws, caller="content")
```
