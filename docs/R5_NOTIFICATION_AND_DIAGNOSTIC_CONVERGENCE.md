# R5 — Notification and Diagnostic Runtime Convergence

**Status: certification candidate.**

R5 closes the route-level notification gap identified by R4 without creating a second execution authority.

## Notification inbox

`GET /api/v1/notifications` and `POST /api/v1/notifications/{notif_id}/read` now use the durable `kanyoza_notifications` repository.

- workspace-scoped reads
- unread filtering
- durable read-state mutation
- bounded page size
- `503` when persistence is unavailable
- `404` when a notification is absent from the authenticated workspace
- no fake empty-inbox success
- no provider transport from the inbox repository

Notification delivery remains owned by existing delivery/execution infrastructure. The repository is only the operator-facing projection and read-state store.

## Director E2E diagnostic

`GET /e2e/director-hands` remains intentionally diagnostic, not an execution API.

- disabled in production
- species/operator restricted
- reports Director capability bindings
- never invokes a capability handler
- never creates an ActionIntent
- never bypasses Governance, Authorization, Action Kernel, Connector Fabric, Receipt, Verification, or Reconciliation

Therefore it is classified as **diagnostic-only**, not an incomplete production mutation surface.

## Authority invariant

```text
Operator UI
  -> authenticated workspace context
  -> notification read model

Consequential action
  -> ActionIntent
  -> Governance
  -> Authorization
  -> Action Kernel
  -> Connector
  -> Provider
  -> Receipt
  -> Verification
  -> Reconciliation
```

R5 adds no second executor or provider transport.
