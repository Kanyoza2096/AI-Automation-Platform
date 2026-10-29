from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Iterable
from uuid import uuid4

from domain.business_activation.facts import (
    Clarification,
    ExtractionRequest,
    ExtractionResult,
    Fact,
    FactType,
    ReviewReextractRequest,
)

MIN_FACT_CONFIDENCE = 0.85

_DAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_DAY_PATTERN = r"(?:" + "|".join(_DAY_NAMES) + r")"
_TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.I)


def _span(text: str, start: int, end: int) -> dict:
    return {"start": start, "end": end, "text": text[start:end]}


def _parse_clock(raw: str) -> str | None:
    match = _TIME_RE.search(raw)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or "00")
    suffix = (match.group(3) or "").lower()
    if minute > 59:
        return None
    if suffix:
        if hour < 1 or hour > 12:
            return None
        if suffix == "pm" and hour != 12:
            hour += 12
        if suffix == "am" and hour == 12:
            hour = 0
    elif hour > 23:
        return None
    return f"{hour:02d}:{minute:02d}"


def _hours_facts(req: ExtractionRequest, extraction_id: str) -> tuple[list[Fact], list[Clarification], list[str]]:
    text = req.operator_text
    facts: list[Fact] = []
    clarifications: list[Clarification] = []
    matched_spans: list[tuple[int, int]] = []

    for match in re.finditer(rf"(?P<days>{_DAY_PATTERN}(?:\s*[-–]\s*{_DAY_PATTERN})?|weekdays|weekends)\s*(?:are|:|-)\s*(?P<body>[^.;\n]+)", text, re.I):
        days = match.group("days").lower()
        body = match.group("body").strip()
        body_start = match.start("body")
        matched_spans.append((match.start(), match.end()))
        if "closed" in body.lower() or "not open" in body.lower():
            value = {"closed": True}
            confidence = 0.98
        else:
            times = list(_TIME_RE.finditer(body))
            if len(times) < 2:
                clarifications.append(Clarification(
                    fact_type=FactType.BUSINESS_HOURS,
                    field="open_window",
                    reason="I found a day schedule but could not identify both opening and closing times.",
                    source_span=_span(text, match.start(), match.end()),
                ))
                continue
            opens = _parse_clock(times[0].group(0))
            closes = _parse_clock(times[1].group(0))
            if not opens or not closes:
                clarifications.append(Clarification(
                    fact_type=FactType.BUSINESS_HOURS,
                    field="open_window",
                    reason="The opening or closing time is ambiguous.",
                    source_span=_span(text, match.start(), match.end()),
                ))
                continue
            tz_match = re.search(r"\b(EAT|CAT|UTC)\b", body, re.I)
            value = {"open": opens, "close": closes, "timezone": tz_match.group(1).upper() if tz_match else None}
            confidence = 0.96 if tz_match else 0.90
        facts.append(Fact(
            workspace_id=req.workspace_id,
            fact_type=FactType.BUSINESS_HOURS,
            subject="business",
            predicate="schedule",
            value={"days": days, **value},
            confidence=confidence,
            source="operator_message",
            source_span=_span(text, match.start(), match.end()),
            asserted_by="operator",
            extraction_id=extraction_id,
        ))

    # Deterministic Sunday closed form, including natural-language shorthand.
    for match in re.finditer(r"\b(sunday)\s+(?:is\s+)?closed\b", text, re.I):
        matched_spans.append((match.start(), match.end()))
        facts.append(Fact(
            workspace_id=req.workspace_id,
            fact_type=FactType.BUSINESS_HOURS,
            subject="business",
            predicate="schedule",
            value={"days": "sunday", "closed": True},
            confidence=0.99,
            source="operator_message",
            source_span=_span(text, match.start(), match.end()),
            asserted_by="operator",
            extraction_id=extraction_id,
        ))

    return facts, clarifications, [text]


def _refund_facts(req: ExtractionRequest, extraction_id: str) -> tuple[list[Fact], list[Clarification], list[str]]:
    text = req.operator_text
    facts: list[Fact] = []
    clarifications: list[Clarification] = []
    matched_spans: list[tuple[int, int]] = []

    for match in re.finditer(r"(?:refund|return)[^.;\n]{0,180}", text, re.I):
        phrase = match.group(0)
        matched_spans.append((match.start(), match.end()))
        day_match = re.search(r"\b(\d{1,3})\s*(?:day|days|d)\b", phrase, re.I)
        if not day_match:
            clarifications.append(Clarification(
                fact_type=FactType.REFUND_POLICY,
                field="window_days",
                reason="I found a refund/return policy but no exact number of days.",
                source_span=_span(text, match.start(), match.end()),
            ))
            continue
        days = int(day_match.group(1))
        if days < 0 or days > 365:
            clarifications.append(Clarification(
                fact_type=FactType.REFUND_POLICY,
                field="window_days",
                reason="The stated return window is outside the supported 0–365 day range.",
                source_span=_span(text, match.start(), match.end()),
            ))
            continue
        unused = bool(re.search(r"\bunused|unopened|unworn\b", phrase, re.I))
        facts.append(Fact(
            workspace_id=req.workspace_id,
            fact_type=FactType.REFUND_POLICY,
            subject="returns",
            predicate="window_days",
            value={"days": days, "condition": "item_unused" if unused else None},
            confidence=0.96 if unused else 0.90,
            source="operator_message",
            source_span=_span(text, match.start(), match.end()),
            asserted_by="operator",
            extraction_id=extraction_id,
        ))

    return facts, clarifications, [text]



_KNOWN_CATEGORY_PATTERNS: tuple[tuple[FactType, tuple[str, ...]], ...] = (
    (FactType.BUSINESS_IDENTITY, ("we are", "our company", "our business", "we operate as")),
    (FactType.BUSINESS_DESCRIPTION, ("we provide", "we offer", "our business", "we specialize", "we help")),
    (FactType.PRODUCT_SERVICE, ("we sell", "we offer", "we provide", "our products", "our services")),
    (FactType.LOCATION, ("located in", "based in", "our shop is in", "our office is in", "we are in")),
    (FactType.DELIVERY_AREA, ("deliver to", "delivery to", "deliver within", "delivery area", "we deliver")),
    (FactType.PAYMENT_METHOD, ("pay by", "payment", "payments", "airtel money", "mobile money", "cash", "bank transfer")),
    (FactType.CONTACT_CHANNEL, ("contact us", "call us", "whatsapp", "email us", "phone number", "reach us")),
    (FactType.CUSTOMER_SEGMENT, ("our customers", "we serve", "target customers", "we cater to")),
    (FactType.PRICING, ("price", "pricing", "costs", "cost", "charged", "fee", "discount")),
    (FactType.FULFILLMENT, ("pickup", "pick up", "delivery", "ship", "shipping", "dispatch")),
    (FactType.STAFF_ROLE, ("manager", "staff", "employee", "salesperson", "cashier", "team")),
    (FactType.ESCALATION_RULE, ("escalate", "manager", "complaint", "urgent", "supervisor")),
    (FactType.INTEGRATION, ("integrated with", "integration", "connects to", "connected to", "uses stripe", "uses shopify")),
    (FactType.FAQ, ("customers ask", "frequently asked", "faq", "people ask")),
    (FactType.BUSINESS_RULE, ("must", "only", "required", "not allowed", "allowed", "rule")),
)


def _infer_category(sentence: str) -> FactType:
    lowered = sentence.lower()
    for category, patterns in _KNOWN_CATEGORY_PATTERNS:
        if any(pattern in lowered for pattern in patterns):
            return category
    return FactType.BUSINESS_KNOWLEDGE


def _generic_business_knowledge(req: ExtractionRequest, extraction_id: str) -> tuple[list[Fact], list[Clarification], list[str]]:
    """Capture explicit operator statements that have no specialized parser yet.

    This is deliberately conservative: the system stores the operator's own
    statement verbatim with its exact source span. It does not invent fields,
    normalize unsupported values, or treat speculative language as confirmed.
    """
    text = req.operator_text
    facts: list[Fact] = []
    unparsed: list[str] = []
    sentence_matches = list(re.finditer(r"[^.!?\n]+(?:[.!?]|$)", text))
    for match in sentence_matches:
        sentence = match.group(0).strip()
        if len(sentence) < 4:
            continue
        # Keep uncertain/speculative claims for clarification rather than facts.
        if re.search(r"\b(maybe|might|possibly|probably|i think|not sure|perhaps)\b", sentence, re.I):
            unparsed.append(sentence)
            continue
        category = _infer_category(sentence)
        facts.append(Fact(
            workspace_id=req.workspace_id,
            fact_type=category,
            subject="business",
            predicate="statement",
            value={"text": sentence, "category": category.value},
            confidence=0.86,
            source="operator_message",
            source_span=_span(text, match.start(), match.end()),
            asserted_by="operator",
            extraction_id=extraction_id,
        ))
    return facts, [], unparsed


def extract_business_facts(req: ExtractionRequest) -> ExtractionResult:
    fingerprint = hashlib.sha256((req.workspace_id + "\0" + req.operator_text.strip() + "\0" + ",".join(sorted(item.value for item in req.fact_types_in_scope))).encode("utf-8")).hexdigest()
    extraction_id = f"extract-{fingerprint[:32]}"
    proposed: list[Fact] = []
    clarifications: list[Clarification] = []
    consumed: list[str] = []

    if FactType.BUSINESS_HOURS in req.fact_types_in_scope:
        f, c, u = _hours_facts(req, extraction_id)
        proposed.extend(f)
        clarifications.extend(c)
        consumed.extend(u)
    if FactType.REFUND_POLICY in req.fact_types_in_scope:
        f, c, u = _refund_facts(req, extraction_id)
        proposed.extend(f)
        clarifications.extend(c)
        consumed.extend(u)
    if FactType.BUSINESS_KNOWLEDGE in req.fact_types_in_scope:
        # Broad mode: run the specialized parsers first, then preserve every
        # remaining explicit business statement as typed, reviewable knowledge.
        broad_types = set(req.fact_types_in_scope) | {
            FactType.BUSINESS_HOURS, FactType.REFUND_POLICY,
        }
        broad_req = req.model_copy(update={"fact_types_in_scope": list(broad_types)})
        specialized = extract_business_facts(broad_req) if broad_types != set(req.fact_types_in_scope) else None
        if specialized is not None:
            # v1.1 — FIX FK MISMATCH ON RECURSIVE MERGE:
            #
            # extract_business_facts(broad_req) computes its own extraction_id
            # from the (workspace_id, operator_text, sorted_fact_types)
            # fingerprint. Because broad_req.fact_types_in_scope differs from
            # req.fact_types_in_scope, the fingerprint differs, so the child's
            # extraction_id differs from the parent's extraction_id.
            #
            # If we merged those facts into proposed_facts as-is, each fact
            # would carry the CHILD's extraction_id while the parent's
            # ExtractionResult carries the PARENT's extraction_id. The
            # repository then inserts the extraction row with the parent's
            # extraction_id and tries to insert facts referencing the child's
            # extraction_id — FK violation:
            #
            #   Key (extraction_id)=(extract-6e04...) is not present in
            #   table "business_activation_extractions"
            #
            # Fix: rewrite each child fact's extraction_id to the parent's,
            # so every fact in the merged list references the extraction row
            # that record_extraction() actually persists.
            for f in specialized.proposed_facts:
                proposed.append(f.model_copy(update={"extraction_id": extraction_id}))
            clarifications.extend(specialized.clarifications)
        generic, generic_clarifications, generic_unparsed = _generic_business_knowledge(req, extraction_id)
        existing_spans = {(f.source_span.get("start"), f.source_span.get("end")) for f in proposed}
        proposed.extend(f for f in generic if (f.source_span.get("start"), f.source_span.get("end")) not in existing_spans)
        clarifications.extend(generic_clarifications)
        consumed.extend(generic_unparsed)

    proposed = [fact for fact in proposed if fact.confidence >= MIN_FACT_CONFIDENCE]
    confidence = min((fact.confidence for fact in proposed), default=0.0)
    if clarifications and confidence:
        confidence = min(confidence, 0.84)

    # This v1 extractor intentionally does not pretend to parse arbitrary prose.
    # Unmapped input remains visible to the operator instead of becoming a guess.
    unparsed = [req.operator_text] if not proposed and not clarifications else []
    return ExtractionResult(
        extraction_id=extraction_id,
        proposed_facts=proposed,
        clarifications=clarifications,
        unparsed_text=unparsed,
        extraction_confidence=round(confidence, 3),
        model_used="deterministic-business-activation-v2-generalized",
        extracted_at=datetime.now(timezone.utc),
        input_fingerprint=fingerprint,
    )


def reextract_review(req: ReviewReextractRequest) -> ExtractionResult:
    """Re-extract one reviewed fact type without persisting or confirming anything.

    The original operator text remains evidence; correction_text is an explicit
    operator correction/clarification scoped to one fact type.
    """
    merged = f"{req.operator_text.strip()}\n{req.correction_text.strip()}"
    return extract_business_facts(ExtractionRequest(
        workspace_id=req.workspace_id,
        operator_text=merged,
        fact_types_in_scope=[req.fact_type],
    ))
