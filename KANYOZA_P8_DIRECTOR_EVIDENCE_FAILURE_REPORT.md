# Kanyoza P8 — Director Observer Evidence Failure Semantics

## Finding

`FunctionObserver.observe()` converted any observer exception into `Health.CRITICAL`. A transport timeout, exception in a health handler, or adapter failure is not evidence that the engine itself is critical. Because Director diagnosis intentionally excludes `UNKNOWN` observations, this conversion could feed false root-cause signals into diagnosis and planning.

## Change

- Observer exceptions now produce `Health.UNKNOWN` and an `observer_failure` issue with `evidence_confidence: low`.
- The exception text remains sanitized.
- Existing explicit source statuses remain unchanged; an engine that reports `critical` still yields critical evidence. Only the failed probe's unsupported inference changed.
- Added a regression test proving probe failure cannot be promoted into an engine-critical state.

## Validation

- Compilation of changed module and tests: passed.
- Focused Director / topology / route / health-truth suites: **114 passed**.
- Full repository suite is still not certified green because the previously isolated publishing pipeline test fails in the current environment when the `supabase` package is unavailable.

## Safety effect

This reduces false-positive diagnosis. A missing or failed observation remains visible in issue details but is not treated as a verified engine condition. Automated remediation still requires fresh, explicit, non-conflicting source evidence and existing Director policy gates.
