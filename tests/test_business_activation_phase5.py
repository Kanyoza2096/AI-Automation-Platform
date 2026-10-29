from __future__ import annotations

import asyncio

from domain.business_activation.facts import BusinessKnowledgeReadQuery, CurrentFactsQuery, ExtractionRequest, FactType
from repositories.business_activation_knowledge_repository import BusinessActivationKnowledgeRepository
from services.business_activation_extraction import extract_business_facts
from services.business_activation_knowledge import BusinessActivationKnowledgeService


def _result(text: str, fact_type=FactType.BUSINESS_HOURS):
    return extract_business_facts(ExtractionRequest(
        workspace_id="ws-1", operator_text=text, fact_types_in_scope=[fact_type]
    ))


def test_confirmation_persists_only_after_explicit_confirmation():
    repo = BusinessActivationKnowledgeRepository()
    service = BusinessActivationKnowledgeService(repo)
    result = _result("We are open Monday-Friday 9:00-17:00 EAT.")
    service.record_extraction(result, "We are open Monday-Friday 9:00-17:00 EAT.", "ws-1", ["business_hours"])
    assert service.current(CurrentFactsQuery(workspace_id="ws-1", fact_type=FactType.BUSINESS_HOURS)).__len__() == 0
    facts, event_id, replay = asyncio.run(service.confirm("ws-1", result.extraction_id, [result.proposed_facts[0].fact_id], "operator@example.com"))
    assert facts[0].confirmed is True
    assert facts[0].confirmed_at is not None
    assert replay is False
    assert event_id is None


def test_replay_is_idempotent_and_does_not_create_second_version():
    repo = BusinessActivationKnowledgeRepository()
    service = BusinessActivationKnowledgeService(repo)
    result = _result("We are open Monday-Friday 9:00-17:00 EAT.")
    service.record_extraction(result, result.model_dump_json(), "ws-1", ["business_hours"])
    first, _, replay1 = asyncio.run(service.confirm("ws-1", result.extraction_id, [result.proposed_facts[0].fact_id], "operator@example.com"))
    second, _, replay2 = asyncio.run(service.confirm("ws-1", result.extraction_id, [result.proposed_facts[0].fact_id], "operator@example.com"))
    assert replay1 is False
    assert replay2 is True
    assert [f.fact_id for f in first] == [f.fact_id for f in second]
    assert len(service.current(CurrentFactsQuery(workspace_id="ws-1", fact_type=FactType.BUSINESS_HOURS))) == 1


def test_new_confirmation_supersedes_previous_active_fact():
    repo = BusinessActivationKnowledgeRepository()
    service = BusinessActivationKnowledgeService(repo)
    a = _result("We are open Monday-Friday 9:00-17:00 EAT.")
    b = _result("We are open Monday-Friday 9:00-18:00 EAT.")
    service.record_extraction(a, "a", "ws-1", ["business_hours"])
    service.record_extraction(b, "b", "ws-1", ["business_hours"])
    asyncio.run(service.confirm("ws-1", a.extraction_id, [a.proposed_facts[0].fact_id], "operator@example.com"))
    asyncio.run(service.confirm("ws-1", b.extraction_id, [b.proposed_facts[0].fact_id], "operator@example.com"))
    current = service.current(CurrentFactsQuery(workspace_id="ws-1", fact_type=FactType.BUSINESS_HOURS))
    assert len(current) == 1
    assert current[0].fact_id == b.proposed_facts[0].fact_id
    historical = service.current(CurrentFactsQuery(workspace_id="ws-1", fact_type=FactType.BUSINESS_HOURS, include_superseded=True))
    assert len(historical) == 2
    assert any(f.fact_id == a.proposed_facts[0].fact_id and f.superseded_by == b.proposed_facts[0].fact_id for f in historical)


def test_runtime_read_excludes_unconfirmed_and_superseded_by_default():
    repo = BusinessActivationKnowledgeRepository()
    service = BusinessActivationKnowledgeService(repo)
    result = _result("We are open Monday-Friday 9:00-17:00 EAT.")
    service.record_extraction(result, "text", "ws-1", ["business_hours"])
    assert service.current(CurrentFactsQuery(workspace_id="ws-1", fact_type=FactType.BUSINESS_HOURS)) == []


def test_workspace_isolation():
    repo = BusinessActivationKnowledgeRepository()
    service = BusinessActivationKnowledgeService(repo)
    result = _result("We are open Monday-Friday 9:00-17:00 EAT.")
    service.record_extraction(result, "text", "ws-1", ["business_hours"])
    asyncio.run(service.confirm("ws-1", result.extraction_id, [result.proposed_facts[0].fact_id], "operator@example.com"))
    assert service.current(CurrentFactsQuery(workspace_id="ws-other", fact_type=FactType.BUSINESS_HOURS)) == []


class _FakeBus:
    def __init__(self):
        self.calls = []
    async def publish(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "evt-test-1"


def test_knowledge_updated_event_emits_once_for_new_confirmation():
    repo = BusinessActivationKnowledgeRepository()
    bus = _FakeBus()
    service = BusinessActivationKnowledgeService(repo, bus)
    result = _result("We are open Monday-Friday 9:00-17:00 EAT.")
    service.record_extraction(result, "text", "ws-1", ["business_hours"])
    asyncio.run(service.confirm("ws-1", result.extraction_id, [result.proposed_facts[0].fact_id], "operator@example.com"))
    asyncio.run(service.confirm("ws-1", result.extraction_id, [result.proposed_facts[0].fact_id], "operator@example.com"))
    assert len(bus.calls) == 1
    assert bus.calls[0][0][0] == "knowledge_updated"
    assert bus.calls[0][1]["workspace_id"] == "ws-1"


def test_runtime_read_returns_citations_for_confirmed_current_knowledge():
    repo = BusinessActivationKnowledgeRepository()
    activation = BusinessActivationKnowledgeService(repo)
    reader = BusinessActivationKnowledgeService(repo)
    result = _result("We are open Monday-Friday 9:00-17:00 EAT.")
    activation.record_extraction(result, "We are open Monday-Friday 9:00-17:00 EAT.", "ws-1", ["business_hours"])
    asyncio.run(activation.confirm("ws-1", result.extraction_id, [result.proposed_facts[0].fact_id], "operator@example.com"))

    read = reader.runtime_read(BusinessKnowledgeReadQuery(workspace_id="ws-1", fact_type=FactType.BUSINESS_HOURS))
    assert read.status == "COVERED"
    assert read.answerable is True
    assert read.handoff_required is False
    assert read.citations[0]["source_span"] == result.proposed_facts[0].source_span
    assert read.citations[0]["extraction_id"] == result.extraction_id


def test_runtime_read_refuses_when_knowledge_is_missing():
    repo = BusinessActivationKnowledgeRepository()
    reader = BusinessActivationKnowledgeService(repo)
    read = reader.runtime_read(BusinessKnowledgeReadQuery(workspace_id="ws-1", fact_type=FactType.REFUND_POLICY))
    assert read.status == "MISSING_KNOWLEDGE"
    assert read.answerable is False
    assert read.handoff_required is True
    assert read.facts == []


def test_runtime_read_distinguishes_unsupported_coverage_gap():
    repo = BusinessActivationKnowledgeRepository()
    reader = BusinessActivationKnowledgeService(repo)
    read = reader.runtime_read(BusinessKnowledgeReadQuery(workspace_id="ws-1"))
    assert read.status == "COVERAGE_GAP"
    assert read.answerable is False
    assert read.handoff_required is True
    assert read.reason == "runtime_read_requires_an_explicit_knowledge_type_or_predicate_scope"


def test_runtime_read_excludes_superseded_version():
    repo = BusinessActivationKnowledgeRepository()
    activation = BusinessActivationKnowledgeService(repo)
    reader = BusinessActivationKnowledgeService(repo)
    a = _result("We are open Monday-Friday 9:00-17:00 EAT.")
    b = _result("We are open Monday-Friday 9:00-18:00 EAT.")
    activation.record_extraction(a, "a", "ws-1", ["business_hours"])
    activation.record_extraction(b, "b", "ws-1", ["business_hours"])
    asyncio.run(activation.confirm("ws-1", a.extraction_id, [a.proposed_facts[0].fact_id], "operator@example.com"))
    asyncio.run(activation.confirm("ws-1", b.extraction_id, [b.proposed_facts[0].fact_id], "operator@example.com"))
    read = reader.runtime_read(BusinessKnowledgeReadQuery(workspace_id="ws-1", fact_type=FactType.BUSINESS_HOURS))
    assert read.status == "COVERED"
    assert [f.fact_id for f in read.facts] == [b.proposed_facts[0].fact_id]


def test_runtime_read_is_workspace_scoped():
    repo = BusinessActivationKnowledgeRepository()
    activation = BusinessActivationKnowledgeService(repo)
    reader = BusinessActivationKnowledgeService(repo)
    result = _result("We are open Monday-Friday 9:00-17:00 EAT.")
    activation.record_extraction(result, "text", "ws-1", ["business_hours"])
    asyncio.run(activation.confirm("ws-1", result.extraction_id, [result.proposed_facts[0].fact_id], "operator@example.com"))
    read = reader.runtime_read(BusinessKnowledgeReadQuery(workspace_id="ws-other", fact_type=FactType.BUSINESS_HOURS))
    assert read.status == "MISSING_KNOWLEDGE"
    assert read.facts == []


def test_runtime_read_general_business_knowledge_wildcard_returns_multiple_categories():
    repo = BusinessActivationKnowledgeRepository()
    activation = BusinessActivationKnowledgeService(repo)
    hours = _result("We are open Monday-Friday 9:00-17:00 EAT.")
    activation.record_extraction(hours, "hours", "ws-1", ["business_hours"])
    asyncio.run(activation.confirm("ws-1", hours.extraction_id, [hours.proposed_facts[0].fact_id], "operator@example.com"))
    broad = _result("We are a furniture shop in Blantyre. We sell locally made sofas.", FactType.BUSINESS_KNOWLEDGE)
    activation.record_extraction(broad, "broad", "ws-1", ["business_knowledge"])
    asyncio.run(activation.confirm("ws-1", broad.extraction_id, [f.fact_id for f in broad.proposed_facts], "operator@example.com"))

    read = activation.runtime_read(BusinessKnowledgeReadQuery(workspace_id="ws-1", fact_type=FactType.BUSINESS_KNOWLEDGE))
    assert read["status"] == "COVERED"
    assert read["answerable"] is True
    assert {item["fact_type"] for item in read["facts"]} >= {"business_hours", "business_identity", "product_service"}
    assert all(item["source_span"] for item in read["citations"])


def test_runtime_read_requires_explicit_scope_and_never_guesses():
    repo = BusinessActivationKnowledgeRepository()
    reader = BusinessActivationKnowledgeService(repo)
    read = reader.runtime_read(BusinessKnowledgeReadQuery(workspace_id="ws-1"))
    assert read["status"] == "COVERAGE_GAP"
    assert read["answerable"] is False
    assert read["refusal_required"] is True
    assert read["facts"] == []
