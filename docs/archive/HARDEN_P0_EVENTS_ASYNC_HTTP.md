# HARDEN P0 — Events workspace_id + Async/HTTP policy

## Nerve contract (`core/nerve.py`)
- Missing `workspace_id` → bind from **WorkspaceContext** when available
- Species events → `platform`
- Strict mode rejects empty / `unbound` for tenant events

## UnifiedEventBus
- Auto-fills `workspace_id` from `get_current_workspace()` when omitted

## DomainEvent
- Optional `workspace_id` field for explicit carriage

## HTTP
- Health monitor Gemini fallback uses **HttpClient** (not bare requests)
- Telegram connector uses **HttpClient**
- Prefer `AsyncHttpClient` (httpx) inside async route handlers
- Prefer `HttpClient` (requests pool) inside sync workers/connectors

## Operator note
Tenant publishers should still pass `workspace_id` explicitly; context fill is a safety net, not a substitute for correct emission.
