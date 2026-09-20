"""
Formal Species laws. Architectural checks and runtime guards reference these.
"""

from __future__ import annotations

from typing import Dict

SPECIES_LAWS: Dict[str, str] = {
    "ONE_CANONICAL_IMPLEMENTATION": (
        "Each major subsystem (auth, council, tokens, audit, event bus, outbox) "
        "has exactly one canonical implementation."
    ),
    "WORKSPACE_FROM_AUTH": (
        "Workspace identity is resolved from credentials, never from client body alone. "
        "Species operators may select a workspace explicitly."
    ),
    "PRODUCTION_FAIL_CLOSED": (
        "Production does not silently fall back to in-memory persistence when "
        "database or queue are unavailable."
    ),
    "EXTERNAL_VIA_OUTBOX": (
        "External side effects (publish, notify) go through durable job/outbox paths."
    ),
    "REQUEST_CONTEXT_REQUIRED": (
        "Business logic runs only after RequestContext is bound."
    ),
    "PLUGIN_NO_CLIENT_WORKSPACE": (
        "Plugins ignore client-provided workspace_id for non-species principals."
    ),
    "CHAOS_FORBIDDEN_IN_PROD": (
        "Chaos engineering requires explicit secure authorization in production."
    ),
}


def assert_law(law_key: str) -> str:
    if law_key not in SPECIES_LAWS:
        raise KeyError(f"Unknown species law: {law_key}")
    return SPECIES_LAWS[law_key]
