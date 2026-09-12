"""
Optional Council LLM enrichment via LLM Gateway.

Default Council path remains LLM-free and deterministic.
Enable with COUNCIL_LLM_SUMMARY=1 for a single post-consensus narrative only.
Never uses LLM to approve/reject actions — policy stays authoritative.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger("kanyoza.council.llm_enrichment")


def maybe_summarize_consensus(
    decision: str,
    votes: Any,
    evidence: Optional[Dict[str, Any]] = None,
    *,
    workspace_id: str = "platform",
) -> Optional[str]:
    if os.environ.get("COUNCIL_LLM_SUMMARY", "").lower() not in ("1", "true", "yes"):
        return None
    try:
        from ai.gateway import get_llm_gateway
        prompt = (
            "Summarize this council outcome in 2 sentences for an operator. "
            "Do not change the decision. Facts only.\n"
            f"Decision: {decision}\n"
            f"Votes: {str(votes)[:800]}\n"
            f"Evidence: {str(evidence or {})[:500]}\n"
        )
        result = get_llm_gateway().complete(
            prompt,
            workspace_id=workspace_id,
            capability="chat",
            temperature=0.3,
            max_tokens=180,
            caller="council",
        )
        if result.ok and result.text:
            return result.text.strip()[:800]
        logger.debug(
            "Council LLM summary degraded mode=%s reason=%s",
            result.mode,
            result.reason,
        )
    except Exception as exc:
        logger.debug("Council LLM summary skipped: %s", exc)
    return None
