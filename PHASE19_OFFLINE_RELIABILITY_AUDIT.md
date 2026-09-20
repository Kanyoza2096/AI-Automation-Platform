# Phase 19 — Offline Reliability & Contract Hardening

## Fixes
- Added explicit monitor target columns used by runtime code (`url`, `expected_status`, `is_paused`) and backfilled them from the existing `config`/`enabled` representation.
- Added monitor workspace/active indexes and `monitor_checks.error_message`.
- Added SSRF validation immediately before monitor outbound requests.
- Disabled blind HTTP redirect following and validate redirect destinations before accepting them.
- Stopped `CommandExecutor` from returning raw exception text to callers; server logs retain diagnostics.

## Verification
- Python compilation: PASS
- Existing regression suite: PASS
