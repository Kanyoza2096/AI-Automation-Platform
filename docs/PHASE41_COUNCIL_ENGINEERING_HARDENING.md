# Phase 41 — Council Engineering Hardening

## Problem addressed

The Council could produce overly permissive decisions because risk routing, quorum, abstentions, and destructive-action policy were not consistently authoritative across the orchestration paths. In particular, a destructive action could reach a council path with an insufficiently strong safety mandate, while some curated proposal types did not include the risk persona.

## Engineering changes

- Destructive/irreversible action classification now has a conservative floor.
- Independent risk assessment can raise, never lower, the action-class risk.
- High/critical proposals always route to deep deliberation.
- Tier-3 deliberation uses the full registered council (up to 12 personas), not a small curated panel.
- `risk_manager` is mandatory for high/critical decisions.
- Deep approval threshold raised to 70%; critical approval threshold is 80%.
- Minimum deep quorum remains 60% of council weight.
- High abstention (>35%) forces revision instead of consent-by-silence.
- Critical proposals without an explicit safety plan cannot be approved.
- Irreversible/destructive actions receive a hard risk veto and therefore cannot be silently majority-approved.
- Council cache no longer stores revision/escalation/pending results, avoiding stale governance outcomes while evidence is changing.
- Persona votes now preserve evidence and expose evidence-quality/relevance fields for consensus weighting.

## Governance principle

The Council is a **decision-quality and safety layer**, not a majority-vote decoration. A larger vote count is not sufficient by itself: decisions must have adequate quorum, relevant safety coverage, evidence, and risk-appropriate approval thresholds.

## Verification

275 tests passed, 1 warning.

The warning is the existing `plugins.council` deprecation warning.

## Remaining work

This phase does not claim that the Council is now "intelligent enough" in every domain. The next Council-focused improvement should add evidence-backed adversarial deliberation and outcome-calibrated persona reliability, while the broader KDO sweep continues through publishing, organism/cognitive engines, integrations, security, infrastructure, and observability.
