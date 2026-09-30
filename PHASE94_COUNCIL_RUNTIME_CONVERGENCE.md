# Phase 94 — Council Runtime Convergence

## Objective
Eliminate the last production-service runtime dependency on the deprecated `plugins.council` implementation and ensure all autonomous decision surfaces use the canonical root `council` implementation.

## Findings
Before this phase, `services/director.py` and `services/condition_engine.py` imported `get_council` from `plugins.council.personas`. That legacy implementation exposed a 10-member `CommunityCouncil`, while the canonical `council` package exposes 12 personas including `AffiliateMarketer` and `Theologian`.

This created a real runtime split: Director and Condition Engine could deliberate with a different council composition from the main cognitive/decision plane.

## Changes
- Rewired `services/director.py` to `from council.integration import get_council`.
- Rewired `services/condition_engine.py` to `from council.integration import get_council`.
- Added `tests/test_council_runtime_convergence.py`.
- The canonical integration adapter remains the compatibility boundary for legacy synchronous `vote_on()` callers, while the underlying `CouncilOrchestrator` remains the single deliberation engine.
- `plugins/council` remains present only as a deprecated compatibility package; it is no longer imported by production Director or Condition Engine code.

## Active Council
The canonical runtime council contains 12 personas:
YouthAdvocate, BusinessOwner, CommunityElder, DataAnalyst, RiskManager, TrendSpotter, CustomerVoice, GutFeeling, MoodRing, Rippler, AffiliateMarketer, Theologian.

## Verification
- Council convergence regression tests: **7 passed**.
- Full backend suite: **351 passed**.
- No production-service import of `plugins.council` remains in Director or Condition Engine.

## Security / governance consequence
Council composition is now consistent across the principal autonomous decision paths. This removes a governance blind spot where newer canonical personas could be absent from Director or Condition Engine deliberations.

This phase does not delete the deprecated compatibility package because external/legacy imports may still exist; deletion should be a separately verified breaking-change decision after dependency inventory confirms zero consumers.
