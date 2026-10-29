from __future__ import annotations

from typing import Any, Dict, List, Optional

from api.dependencies import correlation_id_var
from domain.business_activation.facts import (
    BusinessAgentAnswerRequest, BusinessAgentAnswerResult, BusinessKnowledgeReadQuery,
    BusinessKnowledgeReadResult, FACT_TYPES, CurrentFactsQuery, ExtractionResult, FactType,
)
from repositories.business_activation_knowledge_repository import BusinessActivationKnowledgeRepository


class BusinessActivationKnowledgeService:
    """Knowledge Plane service for the Phase 5 confirmation boundary."""

    def __init__(self, repository: BusinessActivationKnowledgeRepository, event_bus=None):
        self.repository = repository
        self.event_bus = event_bus

    def record_extraction(self, result: ExtractionResult, operator_text: str, workspace_id: str, fact_types_in_scope: list[str]) -> None:
        self.repository.record_extraction(result, operator_text, workspace_id, fact_types_in_scope)

    async def confirm(self, workspace_id: str, extraction_id: str, proposed_fact_ids: List[str], asserted_by: str):
        facts, replay = self.repository.confirm(
            workspace_id=workspace_id,
            extraction_id=extraction_id,
            proposed_fact_ids=proposed_fact_ids,
            asserted_by=asserted_by,
        )
        event_id = None
        if self.event_bus and facts and not replay:
            event_id = await self.event_bus.publish(
                "knowledge_updated",
                {
                    "workspace_id": workspace_id,
                    "extraction_id": extraction_id,
                    "fact_ids": [f.fact_id for f in facts],
                    "fact_types": sorted({f.fact_type.value for f in facts}),
                    "confirmed_by": asserted_by,
                    "correlation_id": correlation_id_var.get(),
                },
                source="business_activation_knowledge",
                correlation_id=correlation_id_var.get(),
                workspace_id=workspace_id,
                strict_nerve=True,
            )
        return facts, event_id, replay

    def current(self, query: CurrentFactsQuery):
        return self.repository.current(
            workspace_id=query.workspace_id,
            fact_type=query.fact_type,
            subject=query.subject,
            predicate=query.predicate,
            conditions=query.conditions,
            include_superseded=query.include_superseded,
            limit=query.limit,
        )

    _QUESTION_SCOPE_PATTERNS = (
        (FactType.BUSINESS_HOURS, ("open", "opening hours", "hours", "close", "closing", "when are you open", "what time")),
        (FactType.REFUND_POLICY, ("refund", "return", "returns", "exchange")),
        (FactType.PRODUCT_SERVICE, ("sell", "product", "products", "service", "services", "offer")),
        (FactType.LOCATION, ("located", "location", "where are you", "where is the", "branch", "branches")),
        (FactType.DELIVERY_AREA, ("deliver", "delivery", "delivery area", "ship", "shipping")),
        (FactType.PAYMENT_METHOD, ("payment", "pay", "airtel money", "mobile money", "cash", "bank transfer")),
        (FactType.CONTACT_CHANNEL, ("contact", "phone", "whatsapp", "email", "call")),
        (FactType.PRICING, ("price", "pricing", "cost", "costs", "fee", "fees", "discount")),
        (FactType.FULFILLMENT, ("pickup", "pick up", "fulfillment", "dispatch")),
        (FactType.CUSTOMER_SEGMENT, ("customers", "customer", "who do you serve", "target market")),
        (FactType.STAFF_ROLE, ("manager", "staff", "employee", "cashier", "salesperson")),
        (FactType.INTEGRATION, ("integration", "integrated", "connect", "connected", "shopify", "stripe")),
    )

    @classmethod
    def _infer_question_scope(cls, question: str) -> Optional[FactType]:
        lowered = question.lower()
        matches = [fact_type for fact_type, patterns in cls._QUESTION_SCOPE_PATTERNS if any(pattern in lowered for pattern in patterns)]
        # Exactly one semantic scope is safe. Multiple scopes require the caller
        # to specify one rather than allowing the runtime to choose arbitrarily.
        return matches[0] if len(set(matches)) == 1 else None

    @staticmethod
    def _render_fact(fact) -> str:
        value = fact.value
        if fact.fact_type == FactType.BUSINESS_HOURS and fact.predicate == "schedule" and isinstance(value, dict):
            days = value.get("days", "the stated days")
            if value.get("closed"):
                return f"{days} is closed."
            opening = value.get("open")
            closing = value.get("close")
            timezone = value.get("timezone")
            suffix = f" {timezone}" if timezone else ""
            return f"{days}: {opening}–{closing}{suffix}."
        if fact.fact_type == FactType.REFUND_POLICY and isinstance(value, dict):
            days = value.get("days")
            condition = value.get("condition")
            if condition == "item_unused":
                return f"Refunds/returns are allowed within {days} days when the item is unused."
            return f"Refunds/returns are allowed within {days} days."
        if isinstance(value, dict) and "text" in value:
            return str(value["text"])
        return str(value)

    def answer(self, request: BusinessAgentAnswerRequest) -> BusinessAgentAnswerResult:
        scope = request.fact_type or self._infer_question_scope(request.question)
        if scope is None:
            return BusinessAgentAnswerResult(
                status="COVERAGE_GAP", answerable=False, handoff_required=True, refusal_required=True,
                question=request.question, answer=None,
                reason="question_does_not_map_to_one_explicit_knowledge_scope",
                knowledge_scope=None, workspace_id=request.workspace_id, citations=[], fact_ids=[],
            )

        read = self.runtime_read(BusinessKnowledgeReadQuery(
            workspace_id=request.workspace_id, fact_type=scope, limit=20,
        ))
        if not read.answerable:
            return BusinessAgentAnswerResult(
                status="MISSING_KNOWLEDGE", answerable=False, handoff_required=True, refusal_required=True,
                question=request.question, answer=None, reason=read.reason or "no_confirmed_current_knowledge_covers_the_question",
                knowledge_scope=scope.value, workspace_id=request.workspace_id, citations=[], fact_ids=[],
            )

        rendered = [self._render_fact(fact) for fact in read.facts]
        answer = " ".join(item for item in rendered if item).strip()
        return BusinessAgentAnswerResult(
            status="GROUNDED", answerable=True, handoff_required=False, refusal_required=False,
            question=request.question, answer=answer, reason=None, knowledge_scope=scope.value,
            workspace_id=request.workspace_id, citations=read.citations, fact_ids=[fact.fact_id for fact in read.facts],
        )

    def runtime_read(self, query: BusinessKnowledgeReadQuery):
        """Read only confirmed/current knowledge for a runtime consumer.

        No fact is treated as permission to infer. `business_knowledge` is a
        controlled wildcard over the same Knowledge Plane, not a second store.
        The return value remains a dict for compatibility with existing runtime
        adapters and tests.
        """
        if query.fact_type is None:
            result = BusinessKnowledgeReadResult(
                status="COVERAGE_GAP",
                answerable=False,
                handoff_required=True,
                refusal_required=True,
                reason="runtime_read_requires_an_explicit_knowledge_type_or_predicate_scope",
                facts=[],
                citations=[],
                count=0,
                workspace_id=query.workspace_id,
            )
            return result

        requested_types = list(FACT_TYPES) if query.fact_type == FactType.BUSINESS_KNOWLEDGE else [query.fact_type]
        facts = []
        for fact_type in requested_types:
            facts.extend(self.repository.current(
                workspace_id=query.workspace_id,
                fact_type=fact_type,
                subject=query.subject,
                predicate=query.predicate,
                conditions=query.conditions,
                include_superseded=False,
                limit=query.limit,
            ))

        # A broad wildcard can collect overlapping projections only once.
        unique = {fact.fact_id: fact for fact in facts}
        facts = list(unique.values())[:query.limit]
        citations = [
            {
                "fact_id": fact.fact_id,
                "fact_type": fact.fact_type.value,
                "subject": fact.subject,
                "predicate": fact.predicate,
                "source": fact.source,
                "source_span": fact.source_span,
                "extraction_id": fact.extraction_id,
                "confirmed_at": fact.confirmed_at.isoformat() if fact.confirmed_at else None,
            }
            for fact in facts
        ]
        covered = bool(facts)
        result = BusinessKnowledgeReadResult(
            status="COVERED" if covered else "MISSING_KNOWLEDGE",
            answerable=covered,
            handoff_required=not covered,
            refusal_required=not covered,
            reason=None if covered else "no_confirmed_current_knowledge_covers_the_requested_scope",
            facts=facts,
            citations=citations,
            count=len(facts),
            workspace_id=query.workspace_id,
        )
        return result
