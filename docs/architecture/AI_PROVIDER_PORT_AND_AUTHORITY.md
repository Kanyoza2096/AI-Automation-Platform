# KDO AI Provider Port and Authority Boundary

## Principle

**AI is intelligence, not authority.** AI providers return interpretation evidence. They never create or execute KDO `ActionIntent` objects directly.

## Canonical flow

```text
OBSERVE
  ↓
AI Provider Port
  ↓
Cloudflare / Gemini / OpenAI / other adapters
  ↓
AIReceipt
  ↓
KDO Intelligence / Interpretation
  ↓
ActionIntent (created by KDO)
  ↓
Governance / Authorization
  ↓
Action Kernel
  ↓
ProviderReceipt
  ↓
Canonical Outcome
  ↓
Verification / Reconciliation
```

## Contracts

`AIReceipt` is evidence that an AI provider produced an interpretation. It is not an external side-effect receipt.

A successful `AIReceipt` must contain non-empty `raw_output`, provider/model provenance, a correlation ID, and a request fingerprint. Empty successful output is contract-invalid.

`ProviderReceipt` remains the contract for external side effects. The two receipts are intentionally separate.

## Provider symmetry

Cloudflare AI is a peer provider behind the AI Provider Port. KDO policy selects a provider/model for a task; application code does not depend on a vendor-specific model API.

The first implementation uses the Cloudflare OpenAI-compatible Workers AI endpoint. Cloudflare documents this endpoint at `/accounts/{account_id}/ai/v1/chat/completions`; API-token authentication is used. The model is supplied by policy and can be changed without changing the KDO core.

## Authority rule

The adapter has no Action Kernel dependency and exposes no execution method. `services.ai_interpretation` is the KDO-owned translation boundary that can formulate an `ActionIntent` only from a successful AI receipt and a KDO-owned allowlist/policy.

An AI recommendation that is not in the KDO-permitted action set is rejected. The provider cannot expand the action surface.

## Failure semantics

- provider 4xx / invalid output → `failed`
- provider transport/5xx uncertainty → `uncertain`
- uncertain AI results are not treated as successful intelligence
- no AI result directly authorizes an external side effect
- provider outages can be handled by KDO routing policy or deterministic paths

## Render certification

`scripts/render_full_system_certification.py --live --exercise` can optionally invoke `/api/v1/ai/interpret` when `KDO_RUN_LIVE_AI=1`.

The live AI certification requires:

1. a real Render base URL and bearer token;
2. Cloudflare account ID and Workers AI API token configured on the Render service;
3. a successful `AIReceipt` with non-empty output;
4. explicit proof that `action_intent_created` is false at the provider boundary.

This proves provider invocation and authority separation. It does not, by itself, certify every AI provider or every sector workflow.

## Runtime consumer integration

The AI Provider Fabric is now the preferred inference path for the existing LLM Gateway when Cloudflare Workers AI credentials are configured.

Consumer services do not call Cloudflare directly:

```text
Content Engine / Content Intelligence / AI Chat
                    ↓
               LLM Gateway
                    ↓
             KDO AI Policy
                    ↓
            AI Provider Port
                    ↓
        Cloudflare Workers AI
                    ↓
                AIReceipt
                    ↓
             Consumer result
```

The gateway preserves the authoritative `AIReceipt` on the runtime intelligence result. Structured generation additionally parses and validates the provider output before application use.

Cloudflare is a default provider selection, not a Core dependency. Removing or changing its credentials changes provider availability/routing; it does not change Content Engine, AI Chat, Content Intelligence, Governance, or the Action Kernel contracts.
