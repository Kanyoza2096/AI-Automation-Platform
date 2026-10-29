# R6 — E2E Publish Runtime Boundary Convergence

## Purpose

R6 closes a runtime-ownership boundary around the `/e2e/publish` surface.

The route is an E2E/diagnostic surface, not a production control plane. It is therefore disabled when the application is running in production.

For controlled non-production environments:

- `dry_run=true` remains non-consequential and uses the existing dry-run pipeline.
- `dry_run=false` delegates to `services.publish_orchestration.execute_governed_publish`.
- Real publishing therefore uses the existing DecisionGate + Action Kernel authority.
- The E2E route does not create a second ledger, executor, authorization system, or provider transport.

## Boundary

```text
E2E request
   |
   +-- production? ---- YES ---> 404 / disabled
   |
   +-- dry_run=true ---> non-consequential pipeline only
   |
   `-- dry_run=false ---> DecisionGate
                           |
                           v
                       Action Kernel
                           |
                           v
                    canonical publish path
                           |
                           v
                    Connector / Provider
```

## Security properties

1. E2E publish is not exposed as a production mutation surface.
2. A real E2E publish requires an explicit `decision_id` under strict publish authorization.
3. Missing or invalid authorization fails closed.
4. The route delegates to an existing governed helper rather than invoking provider connectors directly.
5. The existing canonical Action Kernel execution authority remains unchanged.

## Verification

- R6 E2E boundary tests: 3/3
- Phase 60 legacy execution convergence tests: 6/6
- V71.17 runtime wiring tests: 3/3
- R4 runtime ownership certification tests: 4/4
- Route hardening contracts: 2/2
- Combined targeted regression: 18/18 PASS
- Python compileall: PASS
- Runtime ownership certification: PASS
- No live provider or production deployment was claimed.

## Known external requirement

This artifact does not apply migrations, provision provider credentials, or prove a live external publish. Those remain deployment/environment evidence requirements.
