# Phase 37.10 — Complete Backend Route Contract

The control-plane backend contract is now backed by the platform's existing
runtime, repositories, service registries, Meta Engine, DecisionGate, event
bus, organism, metabolism, logging and audit providers.

## Rules

1. Canonical API prefix is `/api/v1`.
2. Authenticated routes use the existing `require_auth` boundary.
3. Workspace-aware routes enforce the authenticated workspace boundary.
4. Runtime routes read authoritative in-process providers where available.
5. Persistent routes read Supabase-backed stores where available.
6. An unavailable provider is reported as unavailable; synthetic healthy values
   are never manufactured.
7. Decision, execution, provider and audit truth remains owned by their
   existing authoritative subsystems.

## Coverage

Director, Meta Engine, security, services/topology, plugins/integrations,
events/logs/audit, predictions/competitors, quality/posts, workflows,
decisions, organism, SLO/monitoring, metabolism and timeline contracts are
now present either as existing authoritative routes or the new backend
contract routes.

`GET /posts/{slug}` supports the canonical post id, stored slug when present,
and completed automation job id, with workspace scoping.

The staging certification runner remains separate from these read surfaces;
route availability does not claim that external Facebook/Supabase/Redis
infrastructure is reachable.
