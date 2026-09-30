# Phase 37.1 — Skill & Capability System

Kanyoza now has a governed, tenant-scoped capability registry. Capabilities are
versioned declarative contracts describing reusable platform abilities.

## Flow

`Strategy Genome → Capability Resolution → Prerequisite/Health/Authority Check → Cognitive Governance → Existing Execution Plane`

The registry does not store credentials, executable strategy code, or handlers and
cannot grant authority. Capability health can be degraded or quarantined, and
capability versions cannot regress.

## Boundary

A capability check is an eligibility result, not an execution authorization. The
existing Guardian, DecisionGate, durable execution ledger, connector governance,
and human approval boundaries remain authoritative.
