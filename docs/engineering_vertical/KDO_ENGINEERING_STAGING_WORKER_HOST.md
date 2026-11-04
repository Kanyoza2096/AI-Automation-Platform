# KDO Engineering Vertical — Staging Worker Host Integration

## What this change composes

The trusted worker entrypoint now has a concrete staging host factory at
`services.kdo_engineering_staging_host:build_host_arguments`. It composes:

- The existing KDO Supabase service-client singleton from application settings.
- The canonical KDO LLM Gateway.
- The canonical Action Kernel, explicitly configured with the same durable Supabase
  client and with in-memory execution disabled.
- A workspace-scoped `kny_` API token for the worker identity.
- The PostgreSQL build queue and task/workspace repositories supplied by the worker
  process.
- A PostgreSQL event adapter using the existing hash-chained
  `kdo_engineering_console_events` table from migration 142.

The factory only constructs dependencies. It does not enable the worker, poll the
queue, launch Docker, execute tasks, deploy code, or modify production.

## Authentication and scope

The worker token must be a dedicated `kny_` API token stored in the staging
`api_tokens` table. Its persisted token record ID must exactly equal
`KDO_ENGINEERING_WORKER_ACTOR_ID`. It must be bound to the configured workspace and
have an operator-level permission (`write`, `operator`, `admin`, or `owner`).

Before the workflow authorizer permits `execute_build`, it rechecks token existence,
revocation, expiry, workspace binding, active workspace status, the configured
actor/tenant/workspace tuple, and the persisted engineering task scope. All other
workflow actions are denied by this worker-specific callback. Council + Action Kernel
authorization remains a separate gate for each consequential worker action.

The factory also requires both Supabase URL and PostgreSQL DSN to match the explicitly
configured `KDO_ENGINEERING_EXPECTED_SUPABASE_PROJECT_REF`. For the current KDO
Testing project, that reference is `kkzshurcmcptleyqjgzo`. A production environment,
different Supabase URL, or mismatched pooler username is rejected.

## Required staging configuration

Populate the variables in `.env.staging` through the secure operator/secret channel.
Never commit real values or paste the worker token into chat:

- `KDO_ENGINEERING_EXPECTED_SUPABASE_PROJECT_REF`
- `KDO_ENGINEERING_DATABASE_URL`
- `KDO_ENGINEERING_WORKER_ID`
- `KDO_ENGINEERING_TENANT_ID` and `KDO_ENGINEERING_WORKSPACE_ID`
- `KDO_ENGINEERING_WORKER_ACTOR_ID` (the persisted API-token record ID)
- `KDO_ENGINEERING_WORKER_API_TOKEN` (dedicated staging `kny_` token)
- `KDO_ENGINEERING_SOURCE_REPOSITORY_PATH`
- `KDO_ENGINEERING_MANAGED_WORKSPACE_ROOT`
- `KDO_ENGINEERING_WORKER_REPAIR_CAPABILITIES` (operator allowlist)
- `KDO_ENGINEERING_WORKER_IMAGE` (immutable digest-pinned image)

The Procfile worker remains disabled by default. Keep
`KDO_ENGINEERING_WORKER_ENABLED=false` until the actual Render runtime has a verified
Docker CLI and daemon, hard cancellation, rootless/user-namespace isolation, disk
quotas, egress controls, and the full staging acceptance tests have passed.

## Live staging schema observation — 2026-11-04

Read-only inspection of the KDO Testing project confirmed these applied migrations:

- `kdo_engineering_shared_build_queue_140`
- `kdo_engineering_shared_metadata_141`
- `kdo_engineering_console_events_142`
- `kdo_engineering_plan_approval_task_scope_151`
- `kdo_engineering_job_reconciliation_audit_152`

The existing task, workspace, plan-review, build-job, reconciliation, and hash-chained
console-event tables are present. No new Supabase migration is required for this host
composition.

The disposable CI acceptance document currently describes a different migration set
that includes migration 150 and 153. Do not blindly apply migration 150 to the live
staging project: reconcile the CI and live migration histories first, then prove the
resulting schemas are equivalent. No migration was applied during this integration
work.

## Calculator task status

The approved calculator task
`8cdcab6d6fd7445d9b1f3568565b5e32` remains in `planned` state. No build job is
currently enqueued for it. No calculator code has been generated and no tests have
been run. The worker has not been enabled or deployed.
