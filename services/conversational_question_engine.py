"""R13-E conversational question engine.

Turns discovery requirements into the smallest safe next question. This layer
only plans/phrases questions; it does not create or confirm knowledge, mutate
the Knowledge Plane, authorize actions, or execute providers.
"""
from __future__ import annotations

import re
from typing import Any

QUESTION_TYPES = ("confirm", "provide", "clarify", "choose", "source")


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _kind(requirement: str) -> str:
    text = requirement.lower()
    if any(x in text for x in ("policy", "rules", "procedure", "standards", "terms")):
        return "source"
    if any(x in text for x in ("types", "methods", "channels", "areas", "locations", "products", "services")):
        return "provide"
    if any(x in text for x in ("requirements", "eligibility", "authority", "owner", "approval")):
        return "clarify"
    return "provide"


def _question(requirement: str, kind: str, known_context: str = "") -> str:
    req = _norm(requirement).rstrip("?.")
    context = _norm(known_context)
    if kind == "source":
        return f"What is the authoritative source for {req}? You can provide a document, policy, or explain the rule here."
    if kind == "clarify":
        return f"What exactly are the requirements for {req}? Please describe the rule, owner, or approval condition that KDO should treat as business-defined."
    if kind == "confirm":
        return f"I have {context}. Is that correct for {req}?"
    if kind == "choose":
        return f"Which option applies to {req}?"
    return f"What should KDO know about {req}? You can describe it here or provide the relevant source."


def plan_next_question(*, requirements: list[str], known_context: str = "", answered_requirements: list[str] | None = None,
                       max_questions: int = 1) -> dict[str, Any]:
    """Select and phrase the next unresolved discovery question(s).

    Requirements are treated as candidate discovery needs, not facts. The
    output is safe to hand to a conversational UI and remains read-only.
    """
    answered = {_norm(x).lower() for x in (answered_requirements or []) if _norm(x)}
    candidates = []
    for requirement in requirements:
        clean = _norm(requirement)
        if not clean or clean.lower() in answered:
            continue
        kind = _kind(clean)
        candidates.append({
            "requirement": clean,
            "question_type": kind,
            "question": _question(clean, kind, known_context),
            "requires_explicit_confirmation": True,
            "answer_is_candidate_evidence": True,
            "creates_knowledge": False,
        })
    selected = candidates[: max(1, int(max_questions))]
    return {
        "schema": "KDO.R13E.ConversationalQuestionPlan/v1",
        "read_only": True,
        "questions": selected,
        "remaining_requirement_count": len(candidates),
        "safety": {
            "does_not_create_knowledge": True,
            "does_not_confirm_knowledge": True,
            "does_not_authorize_actions": True,
            "does_not_execute_providers": True,
        },
    }
