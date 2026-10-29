# Phase 37.7.1 — Staging Evidence & Observability Plane

## Purpose

Turn staging validation outcomes into immutable, machine-readable evidence that can be consumed by operational readiness and promotion tooling.

## Evidence chain

`Probe -> EvidenceRecord -> PromotionReport -> Readiness`

Every record carries a correlation ID, UTC observation timestamp, provenance, status, failure classification, and SHA-256 digest.

## Safety boundaries

- Evidence is observational; it is not execution authority.
- `FAIL` and `UNOBSERVABLE` block promotion.
- Unknown infrastructure is never promoted to healthy.
- Evidence digests detect accidental or unauthorized mutation.
- Provider uncertainty remains explicitly classifiable and cannot become success through reporting.
- The plane does not contain credentials, connector handlers, deployment authority, or production mutation APIs.

## Real staging use

Supply authoritative adapters to `StagingValidationHarness`, collect the result with `StagingEvidenceCollector`, persist/export the `PromotionReport`, and feed its outcome into the existing readiness process.

The current development environment does not certify real Postgres/RLS, Redis, Facebook/Meta APIs, or Docker runtime connectivity. Those checks require the actual staging environment and credentials.
