"""
Guardian action evaluation — publish safety checks.

This is the binding between the publish pipeline and Guardian's authority.
Every publish action must pass through here before execution.

The Guardian is intentionally strict:
  - Empty content = BLOCK
  - Oversized content = BLOCK
  - Forbidden patterns (secrets, tokens) = BLOCK
  - Dry runs = ALLOW (no external side effect)
  - Otherwise = ALLOW with clear reason

This module is imported by:
    domain/publishing/canonical_pipeline.py::_guardian_evaluate
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger("kanyoza.guardian.evaluate")

# Content that must never appear in a live publish
FORBIDDEN_PATTERNS = (
    "password",
    "secret",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "private_key",
    "-----BEGIN",
)

# Maximum caption/content length for live posts
MAX_CONTENT_LENGTH = 5000

# Maximum dry-run content length (slightly larger)
MAX_DRY_RUN_LENGTH = 10000


def evaluate_action(
    action: str,
    workspace_id: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Evaluate a publish action for safety.

    Args:
        action: Action name — "publish", "delete", "scram", etc.
        workspace_id: Tenant workspace identifier.
        payload: Content payload with caption/content and flags.

    Returns:
        Dict with keys:
            status: "allow" | "block" | "warn"
            reason: Human-readable explanation
            mode: "guardian.evaluate_action" (for trace provenance)
    """
    if action != "publish":
        # Non-publish actions pass through without publish-specific checks
        return {
            "status": "allow",
            "reason": f"no_publish_policy_for_{action}",
            "mode": "guardian.evaluate_action",
        }

    payload = payload or {}
    content = payload.get("content", "") or payload.get("caption", "") or payload.get("topic", "") or ""
    content_str = str(content).strip()
    is_dry_run = bool(payload.get("dry_run", False))

    # ─── 1. Dry run ────────────────────────────────────────
    # Dry runs are non-side-effecting simulations; they may intentionally
    # omit content when the caller is exercising control-plane wiring.
    if is_dry_run:
        if len(content_str) > MAX_DRY_RUN_LENGTH:
            return {
                "status": "block",
                "reason": f"content_too_long:{len(content_str)}>{MAX_DRY_RUN_LENGTH}",
                "mode": "guardian.evaluate_action",
            }
        return {
            "status": "allow",
            "reason": "dry_run_allowed",
            "mode": "guardian.evaluate_action",
        }

    # ─── 2. Empty content ──────────────────────────────────
    if not content_str:
        return {
            "status": "block",
            "reason": "empty_content",
            "mode": "guardian.evaluate_action",
        }

    # ─── 3. Content length ─────────────────────────────────
    max_len = MAX_CONTENT_LENGTH
    if len(content_str) > max_len:
        return {
            "status": "block",
            "reason": f"content_too_long:{len(content_str)}>{max_len}",
            "mode": "guardian.evaluate_action",
        }

    # ─── 4. Forbidden patterns ─────────────────────────────
    lowered = content_str.lower()
    for pattern in FORBIDDEN_PATTERNS:
        if pattern.lower() in lowered:
            return {
                "status": "block",
                "reason": f"forbidden_content:{pattern}",
                "mode": "guardian.evaluate_action",
            }

    # ─── 5. Suspicious patterns (warn but allow) ───────────
    suspicious = _detect_suspicious(content_str)
    if suspicious:
        return {
            "status": "warn",
            "reason": f"suspicious_content:{suspicious}",
            "mode": "guardian.evaluate_action",
        }

    # ─── 6. All clear ──────────────────────────────────────
    return {
        "status": "allow",
        "reason": "content_clear",
        "mode": "guardian.evaluate_action",
    }


def _detect_suspicious(content: str) -> Optional[str]:
    """Detect patterns that warrant a warning but not a full block."""
    # Excessive URLs
    urls = re.findall(r"https?://[^\s]+", content)
    if len(urls) > 5:
        return "too_many_urls"

    # Excessive hashtags
    hashtags = re.findall(r"#\w+", content)
    if len(hashtags) > 20:
        return "too_many_hashtags"

    # All caps (shouting)
    alpha_chars = [c for c in content if c.isalpha()]
    if alpha_chars and sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars) > 0.8:
        return "excessive_caps"

    return None


def evaluate_publish(
    workspace_id: str,
    content: Dict[str, Any],
) -> Dict[str, Any]:
    """Convenience wrapper for publish actions."""
    return evaluate_action(
        action="publish",
        workspace_id=workspace_id,
        payload=content,
    )


__all__ = ["evaluate_action", "evaluate_publish"]
