---
name: gemini-2.5-flash thinking tokens
description: gemini-2.5-flash is a thinking model — internal reasoning consumes most of maxOutputTokens, leaving ~80 tokens for actual output. Fix: thinkingBudget=0 for structured calls.
---

## The Rule
Any call to `gemini-2.5-flash` (or other 2.5-series thinking models) that needs a **predictable, complete text output** (JSON, short replies, structured data) MUST pass `thinkingConfig: {"thinkingBudget": 0}` in the generationConfig.

## Why
`gemini-2.5-flash` spends tokens on internal chain-of-thought reasoning before emitting output. With `maxOutputTokens=1400`, the model consumed ~1320 tokens on reasoning and only ~80 on the actual JSON response (~320 chars). The JSON was truncated mid-field with no closing `}`, causing `json.loads()` to fail every time. The symptom was "no JSON object found in response" (rfind("}") == -1).

## How to Apply
In `services/gemini.py`, the `_call()` method accepts `thinking_budget: Optional[int] = None`. Pass `thinking_budget=0` from any method that needs complete, bounded output:

```python
# generate_structured_post — JSON must be complete
raw = self._call(prompt, temperature=self._post_temp,
                 max_tokens=2048, thinking_budget=0)
```

Chat responses and long-form posts do NOT need `thinking_budget=0` — the thinking helps quality there and output length is less critical.

## Confirmed Fix
After setting `thinking_budget=0`, `generate_structured_post("FastAPI vs Flask", "backend")` returned complete JSON in 2.0s and the post was successfully published to Facebook.
