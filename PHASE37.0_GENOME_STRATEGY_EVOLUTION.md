# Phase 37.0 — Digital Genome & Bounded Strategy Evolution

Kanyoza now has a declarative digital genome for strategy/configuration evolution.

## Boundary

A genome may contain action names and JSON-serializable parameters, lineage, generation,
and identity. It may **not** contain executable code, credentials, permissions, or authority.

Variants are bounded and evaluated exclusively through the offline DreamEngine. Evolution
produces ranked candidates; it does not execute a candidate, mutate production state, grant
permissions, or bypass Guardian/DecisionGate.

## Flow

`Experience → Memory → Prediction → Dream → Genome Variant → Fitness → Cognitive/Governance`

The winning genome is still only a strategy candidate. Production execution remains behind
the existing governance and durable execution planes.
