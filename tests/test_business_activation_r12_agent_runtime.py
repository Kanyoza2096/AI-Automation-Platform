from __future__ import annotations

import asyncio

from domain.business_activation.facts import BusinessAgentAnswerRequest, ExtractionRequest, FactType
from repositories.business_activation_knowledge_repository import BusinessActivationKnowledgeRepository
from services.business_activation_extraction import extract_business_facts
from services.business_activation_knowledge import BusinessActivationKnowledgeService


def _service_with_confirmed_hours():
    repo = BusinessActivationKnowledgeRepository()
    service = BusinessActivationKnowledgeService(repo)
    result = extract_business_facts(ExtractionRequest(
        workspace_id="ws-1",
        operator_text="We are open Monday-Friday 9:00-17:00 EAT.",
        fact_types_in_scope=[FactType.BUSINESS_HOURS],
    ))
    service.record_extraction(result, "We are open Monday-Friday 9:00-17:00 EAT.", "ws-1", [FactType.BUSINESS_HOURS.value])
    asyncio.run(service.confirm("ws-1", result.extraction_id, [result.proposed_facts[0].fact_id], "operator@example.com"))
    return service


def test_r12_answers_from_confirmed_current_knowledge_with_citation():
    service = _service_with_confirmed_hours()
    result = service.answer(BusinessAgentAnswerRequest(workspace_id="ws-1", question="What are your opening hours?"))
    assert result.status == "GROUNDED"
    assert result.answerable is True
    assert result.handoff_required is False
    assert "9:00" in result.answer
    assert result.knowledge_scope == "business_hours"
    assert result.citations and result.citations[0]["source_span"]


def test_r12_refuses_missing_knowledge():
    service = BusinessActivationKnowledgeService(BusinessActivationKnowledgeRepository())
    result = service.answer(BusinessAgentAnswerRequest(workspace_id="ws-1", question="What is your refund policy?"))
    assert result.status == "MISSING_KNOWLEDGE"
    assert result.answerable is False
    assert result.handoff_required is True
    assert result.answer is None


def test_r12_distinguishes_ambiguous_scope_from_missing_knowledge():
    service = BusinessActivationKnowledgeService(BusinessActivationKnowledgeRepository())
    result = service.answer(BusinessAgentAnswerRequest(workspace_id="ws-1", question="Tell me about the business."))
    assert result.status == "COVERAGE_GAP"
    assert result.refusal_required is True
    assert result.knowledge_scope is None


def test_r12_explicit_scope_overrides_question_classifier():
    service = _service_with_confirmed_hours()
    result = service.answer(BusinessAgentAnswerRequest(
        workspace_id="ws-1", question="Tell me something about the business.", fact_type=FactType.BUSINESS_HOURS
    ))
    assert result.status == "GROUNDED"
    assert result.knowledge_scope == "business_hours"


def test_r12_never_reads_other_workspace():
    service = _service_with_confirmed_hours()
    result = service.answer(BusinessAgentAnswerRequest(workspace_id="ws-other", question="What are your opening hours?"))
    assert result.status == "MISSING_KNOWLEDGE"
    assert result.fact_ids == []


def test_r12_superseded_fact_is_not_used():
    repo = BusinessActivationKnowledgeRepository()
    service = BusinessActivationKnowledgeService(repo)
    a = extract_business_facts(ExtractionRequest(workspace_id="ws-1", operator_text="We are open Monday-Friday 9:00-17:00 EAT.", fact_types_in_scope=[FactType.BUSINESS_HOURS]))
    b = extract_business_facts(ExtractionRequest(workspace_id="ws-1", operator_text="We are open Monday-Friday 9:00-18:00 EAT.", fact_types_in_scope=[FactType.BUSINESS_HOURS]))
    service.record_extraction(a, "a", "ws-1", [FactType.BUSINESS_HOURS.value])
    service.record_extraction(b, "b", "ws-1", [FactType.BUSINESS_HOURS.value])
    asyncio.run(service.confirm("ws-1", a.extraction_id, [a.proposed_facts[0].fact_id], "operator@example.com"))
    asyncio.run(service.confirm("ws-1", b.extraction_id, [b.proposed_facts[0].fact_id], "operator@example.com"))
    result = service.answer(BusinessAgentAnswerRequest(workspace_id="ws-1", question="When do you close?"))
    assert result.status == "GROUNDED"
    assert "18:00" in result.answer
    assert "17:00" not in result.answer
    assert result.fact_ids == [b.proposed_facts[0].fact_id]


def test_r12_generic_business_knowledge_answer_preserves_operator_wording():
    repo = BusinessActivationKnowledgeRepository()
    service = BusinessActivationKnowledgeService(repo)
    result = extract_business_facts(ExtractionRequest(
        workspace_id="ws-1",
        operator_text="We sell locally made sofas.",
        fact_types_in_scope=[FactType.BUSINESS_KNOWLEDGE],
    ))
    service.record_extraction(result, "We sell locally made sofas.", "ws-1", [FactType.BUSINESS_KNOWLEDGE.value])
    asyncio.run(service.confirm("ws-1", result.extraction_id, [f.fact_id for f in result.proposed_facts], "operator@example.com"))
    answer = service.answer(BusinessAgentAnswerRequest(
        workspace_id="ws-1", question="What products do you sell?", fact_type=FactType.PRODUCT_SERVICE
    ))
    assert answer.status == "GROUNDED"
    assert "locally made sofas" in answer.answer


def test_r12_answer_is_read_only_and_does_not_create_facts():
    service = _service_with_confirmed_hours()
    before = service.current(__import__('domain.business_activation.facts', fromlist=['CurrentFactsQuery']).CurrentFactsQuery(workspace_id="ws-1", fact_type=FactType.BUSINESS_HOURS))
    service.answer(BusinessAgentAnswerRequest(workspace_id="ws-1", question="What are your opening hours?"))
    after = service.current(__import__('domain.business_activation.facts', fromlist=['CurrentFactsQuery']).CurrentFactsQuery(workspace_id="ws-1", fact_type=FactType.BUSINESS_HOURS))
    assert [f.fact_id for f in before] == [f.fact_id for f in after]
