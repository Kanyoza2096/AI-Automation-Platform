# Phase 43 — Council Intelligence Quality & Deliberation Integrity

## Objective

Make Council approval a real governance decision rather than a permissive weighted majority. This phase strengthens the decision mathematics and deliberation completeness without allowing an LLM or persona to become execution authority.

## Changes

- Risk-sensitive approval thresholds:
  - high risk: 70%
  - critical: 80%
- Risk-sensitive rejection thresholds are explicit.
- Quorum failure can never produce approval.
- High/critical deliberations cannot early-terminate before the complete panel has voted.
- High/critical decisions require Risk Manager coverage.
- High/critical decisions require minimum evidence quality.
- Critical decisions require low abstention and stronger evidence requirements.
- Hard vetoes are validated through `VetoPolicy`; an arbitrary persona cannot block an action merely by setting `veto_type=hard`.
- Invalid veto claims are retained as warnings for auditability but do not gain authority.
- Soft vetoes force escalation rather than being overridden by a numerical majority.
- Abstention is treated as a first-class signal rather than disappearing from the decision-quality calculation.

## Authority boundary

Council remains a governance layer. It does not directly execute external side effects. Execution continues through DecisionGate / ExecutionLedger / ExternalActionExecutor and the existing policy boundaries.

## Important limitation

This phase improves governance correctness and deliberation quality metrics. It does **not** prove that persona reasoning is intelligent in a human-like or frontier-AI sense. The next Council intelligence track should evaluate evidence quality, contradiction detection, alternative generation, outcome calibration, and decision quality against real-world outcomes.
