# Phase 36.8 — Predictive + Causal Intelligence

Kanyoza now derives bounded forecasts and causal hypotheses from tenant-scoped episodic execution experience.

## Design

- Predictions use only the workspace's episodic experience history.
- Small samples are smoothed so the organism does not become falsely certain.
- Uncertain execution outcomes are excluded from success/failure prediction and causal samples.
- Predictions are written to the World Model with `predicted` belief status and provenance.
- Causal output is deliberately named a **hypothesis**. Observational execution history cannot by itself prove causation.
- No prediction can grant permissions, bypass Guardian/DecisionGate, alter execution receipts, or directly execute an action.
- All values are bounded, tenant-scoped, and confidence-bearing.

## Authority boundary

Durable execution remains authoritative for what happened. Memory is evidence. The World Model is derived knowledge. Prediction informs planning only; it never becomes execution authority.
