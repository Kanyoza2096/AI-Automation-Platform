# Kanyoza P25 — Director Capability Coverage and Integration Audit

## Objective
Make capability readiness gaps visible and actionable without confusing static handler binding with live integration health or end-to-end execution proof.

## Changes

- Extended the authenticated `GET /capabilities` response with actionable `issue_counts`, `by_sector` coverage, explicit missing-handler and disabled counts, and dependency verification counts.
- Exposed `end_to_end_runtime_verified: false` and `readiness_scope: static_wiring_and_contracts_only` in the summary. A binding report is not treated as a runtime integration test.
- Preserved the per-capability catalog and existing summary fields for compatibility.
- Coverage is computed from the current catalog and does not mutate capability definitions or trigger actions.
- Added regression tests for API coverage totals, issue count consistency, sector aggregation, non-mutation, and explicit unknown runtime state.

## Validation

- Focused P16/P18/P19/P20 and Director regression suite: **159 passed**.
- Python compilation: passed.
- ZIP archive integrity: verified after packaging.

## Scope and limitations

These results are local repository tests. No production services, Supabase connection, external adapters, or side-effecting capabilities were invoked. Runtime dependency health and end-to-end execution remain unverified until deployment-specific checks run in a controlled staging environment.

## Files changed

- `services/director/api.py`
- `tests/test_phase25_capability_coverage_audit.py`
- `KANYOZA_P25_CAPABILITY_COVERAGE_AUDIT_REPORT.md`
