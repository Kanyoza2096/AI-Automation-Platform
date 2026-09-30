# Commerce & E-commerce Sector Pack v1

## Scope

Commerce v1 adds a sector-local domain surface for:

- products and SKU catalog
- customers
- orders and order items
- order lifecycle state
- governed payment-capture intent
- governed fulfillment intent

## Architectural boundary

The pack owns domain models, repository operations, commands, policies, workflows,
events, and declared external actions. It does **not** own consequential provider
execution. Payment, fulfillment, and external notifications are connector-deferred
and must execute through the KDO Action Kernel.

```text
Commerce Pack
     |
     +-- Domain resources
     +-- Policies / workflows / events
     +-- Declared external actions
     |
     v
Action Kernel
     |
Durable Execution Ledger
     |
Connector / Provider
     |
External Receipt -> Canonical Outcome -> Verify / Reconcile
```

## v1 external actions

- `commerce.capture_payment`
- `commerce.fulfill_order`
- `commerce.send_order_notification`

These declarations do not grant the sector pack an execution bypass.

## Tenancy

Commerce execution resolves workspace identity through the canonical
`RequestContext` / `resolve_plugin_workspace` boundary. Client-supplied
`workspace_id` is stripped before domain execution for ordinary principals.

## Production note

The in-memory repository is test/development support only. Production mode
requires durable persistence. Real payment, fulfillment, and notification
providers require explicit connector contracts and production certification.
