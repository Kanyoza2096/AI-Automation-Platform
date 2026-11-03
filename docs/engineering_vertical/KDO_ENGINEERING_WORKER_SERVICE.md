# Engineering Build Worker Polling Service

## Scope

The Phase 16 worker service adds an injectable polling loop around the existing
lease-fenced queue and lease-heartbeat execution supervisor. It claims work only
with an explicit tenant/workspace pair, verifies that the returned claim matches
the worker identity and scope, and delegates execution to the governed
Phase 16.3 path through the heartbeat supervisor.

## Runtime behavior

- Claims use the queue's tenant/workspace filters; unscoped claims are never
  requested by this service.
- Empty queues are polled at a configurable interval.
- Queue claim failures use capped exponential backoff.
- A claimed job is executed once. Execution failures are recorded in the
  returned worker summary and are not automatically retried.
- A malformed or cross-scope claim fails closed before the executor is called.
- A stop event prevents new claims and interrupts idle polling. It does not
  claim to terminate a running child process.
- Result callbacks are observational: their failure is logged separately and
  cannot turn a completed build into a retry candidate.

## Security and governance boundaries

The worker receives the canonical queue, workflow, actor identity, and executor
from its host. It does not construct credentials, select a database, bypass
Council/Action Kernel approval, grant patch authority, or approve releases.
The supplied executor must enforce bounded and cancellable isolated execution.
The heartbeat supervisor prevents terminal completion after a failed lease
renewal; uncertain jobs remain subject to queue reconciliation.

## Remaining production integration

This module is an injectable worker service, not an automatic application
bootstrap. Production still must wire it to the canonical PostgreSQL queue,
a production-authorized workflow actor, a task-scoped workspace provider, and
the pinned containerized quality executor. A real PostgreSQL integration test
must cover concurrent claims, renewal, expiry, and fencing. The deployment host
must also prove graceful process shutdown and worker-crash reconciliation.
No production worker activation, deployment, or release is claimed by this
implementation.
