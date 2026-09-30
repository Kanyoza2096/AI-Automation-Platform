# V73 Intelligence-to-Execution Convergence — Implementation

This patch closes the production handoff gap identified in the V65 WhatsApp path.

## What changed

1. `services/business_action_intent_materializer.py`
   - Adds the trusted parameter materialization boundary.
   - Never copies arbitrary AI/model output into executable parameters.
   - Refuses to invent missing follow-up scheduling data.

2. `services/v73_intelligence_execution_convergence.py`
   - Adds the canonical V65.9 → Action Kernel admission path.
   - Validates workspace identity and handoff fingerprint.
   - Materializes trusted parameters.
   - Calls only `ActionKernel.enqueue()`; it does not call providers directly.
   - Reuses the handoff idempotency key so duplicate delivery is harmless at the Action Kernel boundary.

3. `services/v71_14_production_route_event_wiring.py`
   - Routes `business.decision.action.handoff.ready` directly to the V73 convergence admission.
   - All other business events retain the existing BusinessAutomation path.
   - This removes the need for a second sales-policy execution path for V65.9.

4. `services/business_decision_policy.py` and `services/business_decision_action_handoff.py`
   - Preserve the canonical business intent/message identity needed by the materializer.

5. `routes/webhook.py`
   - Publishes a handoff fingerprint so the consumer can detect tampering/corruption before admission.

6. `tests/test_v73_intelligence_execution_convergence.py`
   - Covers trusted materialization, fail-closed missing scheduling data, duplicate delivery idempotency-key reuse, and production-router convergence.

## Verified

Targeted V65/V73 suite:

- **32 passed, 0 failed**

The test suite validates the new convergence layer without contacting an external provider.

## Important limitation

This closes the **code-path gap**, but it does not claim production provider execution has been proven. The next evidence milestone is a real staging run that proves:

`WhatsApp inbound → V65 understanding → decision → handoff → V73 admission → durable ActionOutbox → worker → CRM/WhatsApp provider evidence → outcome → attribution`

with duplicate webhook delivery resulting in one business effect.
