"""Configuration-only release gate for KDO engineering staging.

This gate never prints secret values and never infers production readiness from
local tests. Live database checks are performed separately by the staging CLI.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

_REQUIRED = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY")
_SECRET_NAMES = frozenset({"SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY", "GEMINI_KEY",
                           "MASTER_API_TOKEN", "ENCRYPTION_KEY", "APP_SECRET"})


def evaluate_staging_configuration(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    values = os.environ if env is None else env
    blockers: list[str] = []
    checks: dict[str, dict[str, Any]] = {}
    for name in _REQUIRED:
        present = bool(str(values.get(name, "")).strip())
        checks[name] = {"configured": present}
        if not present:
            blockers.append(f"{name.lower()}_required")

    raw_url = str(values.get("SUPABASE_URL", "")).strip()
    if raw_url:
        parsed = urlparse(raw_url)
        valid = parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username and not parsed.password
        checks["SUPABASE_URL"]["valid_https_url"] = valid
        if not valid:
            blockers.append("supabase_url_must_be_https_without_embedded_credentials")

    environment = str(values.get("ENVIRONMENT", "")).strip().lower()
    checks["ENVIRONMENT"] = {"value": environment or "unset", "staging": environment == "staging"}
    if environment != "staging":
        blockers.append("environment_must_be_explicitly_staging")

    guardian = str(values.get("GUARDIAN_ENABLED", "false")).strip().lower()
    checks["GUARDIAN_ENABLED"] = {"value": guardian, "disabled": guardian in {"false", "0", "no"}}
    if guardian not in {"false", "0", "no"}:
        blockers.append("autonomous_guardian_must_be_disabled_for_initial_staging")

    # Presence-only diagnostics: never include values or partial secrets.
    checks["optional_integrations"] = {
        name: {"configured": bool(str(values.get(name, "")).strip())}
        for name in sorted(_SECRET_NAMES - set(_REQUIRED))
    }
    return {
        "ready": not blockers,
        "mode": "configuration_only",
        "secrets_redacted": True,
        "checks": checks,
        "blockers": blockers,
        "limitations": [
            "configuration_presence_does_not_prove_credentials_are_valid",
            "does_not_test_tenant_row_level_security",
            "does_not_execute_council_or_action_kernel_actions",
            "does_not_certify_release_readiness",
        ],
    }
