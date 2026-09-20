# Phase 45 — AI Business Understanding

V65 introduces a bounded AI interpretation layer between inbound customer communication and KDO business automation.

## Authority boundary

`customer message → deterministic baseline → optional AI understanding → evidence/confidence → BusinessAutomation/Condition Engine → Action Intent → approval/policy → Action Kernel → external world`

The AI service is deliberately not an agent with tools. It cannot send messages, mutate CRM state, call arbitrary functions, or select an unregistered action.

## Provider contract

When configured, KDO calls an OpenAI-compatible chat-completions endpoint using:

- `KDO_AI_BASE_URL`
- `KDO_AI_API_KEY`
- `KDO_AI_MODEL` (default `gpt-4.1-mini`)

Temperature is fixed to zero and the requested output is a small JSON contract. Evidence is accepted only when it is an exact substring of the customer message. Invalid model output is discarded.

When the provider is missing or unavailable, KDO keeps the deterministic interpreter result. AI is therefore an enhancement, never a hidden availability dependency.

## Safety

AI confidence does not bypass risk, approval, authorization, idempotency, or the Action Kernel. A higher-confidence interpretation only changes the business signal presented to downstream deterministic policy.

## Verification

Run from `backend/kdo19`:

```bash
PYTHONPATH=. pytest -q tests
```

Node builds remain environment-dependent when frontend dependencies are not installed.
