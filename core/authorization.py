"""Canonical authorization vocabulary and role policy.

This module is the single source of truth for workspace RBAC.  Compatibility
layers may import from here, but business code should not maintain its own
role vocabulary.
"""
from __future__ import annotations

from enum import IntEnum


class Role(IntEnum):
    VIEWER = 10
    OPERATOR = 20
    ADMIN = 30
    OWNER = 40
    SPECIES = 100


ROLE_ALIASES = {
    "member": "viewer",
    "editor": "operator",
}

CANONICAL_ROLES = ("viewer", "operator", "admin", "owner", "species")


def normalize_role(role: str | None, *, species: bool = False) -> str:
    if species:
        return "species"
    value = str(role or "viewer").strip().lower()
    value = ROLE_ALIASES.get(value, value)
    return value if value in CANONICAL_ROLES[:-1] else "viewer"


def role_level(role: str | None) -> int:
    normalized = normalize_role(role, species=str(role or "").lower() == "species")
    return int(Role[normalized.upper()])


def at_least(user_role: str | None, minimum_role: str) -> bool:
    return role_level(user_role) >= role_level(minimum_role)
