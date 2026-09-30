# Phase 44 — Closed-Loop Customer Communication

KDO now has the first governed customer-communication loop for Sales Operations.

## Inbound

WhatsApp Cloud API inbound messages are resolved to the exact enabled WhatsApp social account using `metadata.phone_number_id`. The account supplies the workspace/brand scope; no caller-provided workspace is trusted.

The webhook emits `customer.message.received` onto the Unified Event Bus with a stable provider message ID. Duplicate deliveries are fenced by message ID. Failed publication leaves the fence retryable.

## Outbound

`whatsapp_message` is a high-risk Action Kernel contract and requires approval for live execution. It requires an exact `social_account_id`, recipient number, and message body. The selected account must belong to the same workspace and be a WhatsApp account.

The existing WhatsApp Cloud connector remains the provider adapter. The automation layer never calls it directly.

## Automation

The starter template `sales.follow-up-whatsapp` turns `lead.follow_up_due` into a governed WhatsApp message. It is deliberately approval-controlled because outbound customer communication is consequential.

## Boundary

This phase does not claim unrestricted autonomous WhatsApp messaging. Provider policy, customer consent, template/session rules, account health, and production credentials still govern whether a message can actually be sent.

The next layer is response interpretation: map inbound customer replies into bounded sales signals, then let the existing state machine and approval policy decide the next action.
