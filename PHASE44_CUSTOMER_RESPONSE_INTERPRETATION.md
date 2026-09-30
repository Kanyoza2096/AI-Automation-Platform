# Phase 44 — Customer Response Interpretation

KDO V62 adds a bounded interpretation layer for inbound customer messages.

The interpreter converts message text into a small audited vocabulary:
`purchase_interest`, `price_request`, `meeting_request`, `objection`, `opt_out`,
and `general_inquiry`.

Interpretation is not an action. It does not send messages, change CRM state, or
call external providers. It enriches the trusted `customer.message.received`
event. Downstream business automation remains responsible for decisions and all
consequential actions still pass through the Action Kernel.

Opt-out is intentionally prioritized so a message containing both commercial
interest and an explicit stop request produces `opt_out`.

This phase is a deterministic foundation. It does not claim semantic equivalence
to an LLM or human understanding. Future AI-assisted interpretation must preserve
the same bounded signal contract, confidence/evidence fields, tenant scope, and
governed action boundary.
