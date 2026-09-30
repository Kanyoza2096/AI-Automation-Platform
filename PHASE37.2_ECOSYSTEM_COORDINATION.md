# Phase 37.2 — Ecosystem / Multi-Organism Coordination

Kanyoza now supports explicit, bounded coordination between tenant organisms through an opt-in ecosystem boundary.

## Authority boundary

Signals are declarative observations/opportunities only. The ecosystem plane cannot execute actions, grant permissions, move credentials, mutate durable execution truth, or bypass Guardian/DecisionGate.

## Isolation boundary

A workspace must be an explicit ecosystem member and can receive only signals targeted to that workspace. Signals are bounded, expire when configured, and reject obvious credential/authority/code channels.

## Design principle

Multi-organism coordination is a communication layer, not a new authority plane. Existing governance and durable execution remain authoritative.
