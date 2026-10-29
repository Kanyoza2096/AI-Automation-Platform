# KDO AI Engineering Vertical — Phase 15 Completion and Staging Package

## Scope

This phase consolidates the repository-side work needed to prepare a controlled staging deployment. It does not claim the cloud deployment itself has happened.

## Delivered

- `services/kdo_engineering_release_gate.py`: configuration gate that validates required staging configuration, HTTPS Supabase URL, explicit `ENVIRONMENT=staging`, and `GUARDIAN_ENABLED=false`. It emits presence-only diagnostics and never returns secret values.
- `scripts/kdo_engineering_staging_gate.py`: command-line config gate and optional read-only database schema preflight. The live probe uses the RLS-aware anon client and performs zero-row selects only.
- `deployment/staging/docker-compose.yml`: isolated initial staging service bound to loopback, non-root image from the repository Dockerfile, dropped Linux capabilities, `no-new-privileges`, bounded tmpfs, health check, and Guardian disabled.
- `deployment/staging/.env.staging.example`: placeholder-only environment template.
- `.gitignore` entries for staging secrets and state.
- `KDO_AI_ENGINEERING_VERTICAL_COMPLETION_AND_STAGING_GUIDE.md`: deployment instructions, architecture boundaries, required live acceptance evidence, and release blockers.
- Unit tests for missing secrets, invalid URL, unsafe staging flags, and redaction.

## Validation performed

- Focused regression command: `pytest -q tests/engineering_vertical tests/reliability/test_execution_ledger.py tests/test_condition_engine_council_approval_fence.py`
- Result: **121 passed**.
- Python compilation: passed for release-gate, staging-preflight, and CLI modules.
- CLI config-only mode: verified it fails closed when configuration is absent and passes when required placeholder values are present; output contains no secret values.
- Compose YAML parsed successfully with PyYAML.

## Not performed / external dependencies

- Docker Engine/Compose is not available in this execution environment, so the image was not built and the staging container was not started here.
- No staging credentials, external deployment account, or approved endpoint were provided. No remote deployment or live database query was performed.
- Live Council authorization, production Action Kernel with durable ledger, cross-tenant RLS negative tests, real provider side effects, distributed worker recovery, and sandbox isolation remain staging acceptance requirements.

## Release posture

**Repository-side staging preparation: implemented and locally validated. Live staging deployment: pending authorized environment access. Production release: blocked until the live acceptance checklist in `KDO_AI_ENGINEERING_VERTICAL_COMPLETION_AND_STAGING_GUIDE.md` has been completed and reviewed.**
