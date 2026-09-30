# IPC-19 — Production Autonomy Certification Gate

## Result

**Certification: NOT_PROVEN**

IPC-19 formalizes the final production-autonomy gate. The available environment successfully exercised the offline reconciliation, workforce-chaos, duplicate-delivery, multiprocess, and distributed-certification evidence, but it cannot certify real PostgreSQL concurrency because the required live PostgreSQL/Docker/psycopg runtime is unavailable.

## Evidence

- Production-focused regression suite: `returncode=0`
- Offline external reconciliation: `PASS`
- Offline workforce chaos: `PASS`
- Real PostgreSQL concurrency: `NOT_PROVEN`

### Real PostgreSQL gate evidence

`psql_available=False`

`docker_available=False`

`psycopg_available=False`

Therefore IPC-19 **must not be represented as production-certified** yet. The source-level architecture and offline safety evidence are converged; live concurrency certification remains an explicit gate.

## Required live certification

The final gate must exercise at minimum:

1. Concurrent claims against real PostgreSQL.
2. Lease expiry and takeover across separate worker processes.
3. Crash after provider side effect and reconciliation.
4. Duplicate delivery under concurrent workers.
5. Transaction rollback and retry.
6. Multi-instance restart/failover.
7. Provider timeout/ambiguous receipt handling.
8. Credential revocation during execution.
9. Cross-workspace isolation under concurrent load.
10. Objective/cycle/run lineage preservation through recovery.

**No offline test substitutes for these live checks.**
