# KDO Connector Fabric — Curated Provider Catalog

KDO exposes curated, governed provider capabilities through the Connector Fabric. Providers do not become execution authorities; the Action Kernel remains the authoritative execution plane.

## Providers

| Provider | Primary KDO capability examples | Mutation semantics |
|---|---|---|
| GitHub | issues, comments, workflows, PR merge | approval-gated; workflow/control operations reconcile |
| Render | service/deployment inspection, deploy/cancel | deployment acceptance is uncertain until verified |
| Stripe | payment-intent creation/inspection | idempotency-key required for creation |
| AWS | ECS service scaling, S3 object writes | SDK/role credentials; ambiguous writes reconcile |
| Shopify | order inspection/cancellation | cancellation is high-risk |
| Slack | operational messages/channel inspection | governed notification write |
| Jira | issue inspection/creation | governed work-management write |
| PagerDuty | incident inspection/creation | incident creation is high-risk |
| Datadog | monitor inspection/downtime creation | governed operational mutation |
| Cloudflare | zone inspection/cache purge | purge remains uncertain without durable receipt identity |
| Twilio | message inspection/SMS sending | governed communication write |

## Rules

1. Provider adapters are replaceable infrastructure, not business logic.
2. Consequential mutations require explicit KDO governance/approval.
3. Mutation transport failures are never blindly retried.
4. A provider success without authoritative identity is not upgraded to terminal success.
5. Uncertain outcomes enter reconciliation rather than duplicate execution.
6. Credentials are adapter configuration, never action parameters.
7. Sector packs declare capabilities/actions; the core execution plane remains unchanged.

## AWS note

AWS uses SDK/credential-chain semantics rather than pretending all AWS APIs are a single bearer-token REST provider. The adapter is intentionally narrow and accepts an injected client/session for deterministic certification.

## Google Workspace

Google Workspace is supported as a unified enterprise connector boundary covering curated Gmail, Drive, Calendar, Sheets, and Docs capabilities. See `GOOGLE_WORKSPACE_CONNECTOR.md` for the current action surface and production-certification boundary.

## AI Provider Fabric

AI providers use a dedicated provider-neutral `AIProviderPort` and `AIReceipt` contract. They are peer providers in the Connector Fabric, but their output is interpretation evidence rather than an external side-effect receipt.

| Provider | Boundary | Initial use | Authority |
|---|---|---|---|
| Cloudflare AI | `CloudflareAIAdapter` | text interpretation / classification / summarization | interpretation only |

KDO owns provider/model selection through `services/ai_policy.py`. AI recommendations must pass through KDO interpretation and policy before an `ActionIntent` can exist.
