# Phase 38 — CRM Durability Hardening

## Status
Implemented in the content-intelligence/business-automation continuation artifact.

## Problem removed
The CRM repository previously swallowed Supabase write failures and then returned an in-memory object as if the write had succeeded. In an automation workflow this could produce a completed execution receipt for data that never reached the authoritative database.

## Changes
- CRM contact/deal/follow-up/meeting writes now fail closed when an authoritative Supabase write fails.
- Production runtime refuses CRM writes when no durable Supabase client is available.
- All CRM write paths require a non-empty workspace through `require_workspace_id`.
- Business Automation CRM adapters now treat a domain result of `{"ok": false}` as an execution failure instead of recording success.
- Existing non-production/in-memory behavior remains available for isolated unit tests and local library use.

## Reliability boundary
The Business Automation execution reservation remains the idempotency fence. CRM persistence is now authoritative when configured; a failed persistence operation cannot be converted into a successful automation result by the adapter layer.

## Verification
- Full test suite: **267 passed, 1 warning**.
- The warning is the existing `plugins.council` deprecation warning.

## Still required before production certification
This phase does not prove real Supabase RLS, RPC locking, Redis multi-process behavior, provider delivery, or deployment dependency completeness. Those remain staging gates.
