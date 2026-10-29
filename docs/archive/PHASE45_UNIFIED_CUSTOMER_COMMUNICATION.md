# Phase 45 — Unified Customer Communication

KDO now exposes one governed `customer_communication` action for customer-facing outbound communication across the bounded `email` and `whatsapp` channels.

The business automation layer produces one Action Intent. The Action Kernel owns authorization, approval, idempotency and durable execution; the communication service only performs provider transport after the kernel has admitted the intent.

Email uses SMTP configuration (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_ADDRESS`, optional `SMTP_USE_TLS`). WhatsApp continues to resolve an exact tenant-scoped connected account.

The unified action deliberately does not introduce arbitrary provider URLs, arbitrary headers, arbitrary scripts, or direct connector calls from automation rules.

This phase standardizes outbound communication semantics. Inbound email provider adapters remain a later provider-specific integration concern; existing WhatsApp inbound events continue through `customer.message.received`.
