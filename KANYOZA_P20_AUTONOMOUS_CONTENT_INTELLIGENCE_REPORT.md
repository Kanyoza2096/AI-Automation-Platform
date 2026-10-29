# Kanyoza P20 — Autonomous Content Intelligence

## Objective
Extend the Director's diagnostic and advisory capabilities using real autonomous-content quality measurements without inventing schedule failures or bypassing Director governance.

## Changes
- Added measured quality diagnostics to `AutonomousContentEngine.check_health()` under the existing `metrics.director_signals/v1` envelope.
- Added counts for valid quality samples, samples below threshold, average valid quality score, low-quality brands, today's recorded posts, and brands with post history.
- Scores are accepted only when they are finite numeric values in the documented 0–100 range. Missing, malformed, NaN, infinite, and out-of-range values are excluded.
- A low-quality advisory requires at least three valid recorded scores for a brand, evaluated over its last five valid scores, with their average strictly below 60/100.
- Advisories are emitted only while the autonomous content engine is running, and are bounded to ten brands per health snapshot.
- Added the Director capability `review_content_quality` in `Sector.CONTENT`, with `Authority.NOTIFY` and a `brand_id` target. This is an advisory, not permission to publish content or execute a remediation automatically.
- Added tests for no-data behavior, insufficient evidence, malformed data, high scores, stopped-engine behavior, and successful Director signal validation.

## Validation
- Focused regression suite: **143 passed**.
- Python compilation: **passed** (`python -m compileall -q core services tests`).
- Full-suite attempt: stopped at an unrelated environment-sensitive failure in `tests/pipeline/test_publish_end_to_end_contract.py::test_connector_receives_brand_for_tenant_specific_facebook_credentials`. The test refuses to proceed because the Supabase Python package/client is unavailable (`No module named 'supabase'`; no Supabase client available). At that point, 655 tests had passed before the first failure. A subsequent unrestricted full-suite attempt exceeded the execution time limit.
- This package does not claim production runtime, live Supabase connectivity, or actual posting-path validation.

## Safety and evidence boundaries
- No schedule gap is inferred from missing recent posts; the existing state does not establish an authoritative expected schedule.
- No signal is emitted merely because the engine has no quality samples.
- Director freshness, observer health, sector matching, capability enablement, and governance checks remain in force.
- Quality review signals are advisory and do not bypass authorization or execution controls.

## Files changed
- `core/autonomous_content.py`
- `services/director/core.py`
- `tests/test_phase20_autonomous_content_intelligence.py`
