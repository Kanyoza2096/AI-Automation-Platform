from __future__ import annotations
from datetime import datetime, timezone
from domain.business_activation.facts import ExtractionRequest, FactType
from domain.business_discovery.planner import BusinessDiscoveryPlanner
from domain.business_discovery.discovery import (
    ContinueDiscoveryRequest, DiscoveryGap, DiscoverySession, DiscoveryStatus, DiscoveryTurn,
    StartDiscoveryRequest,
)
from repositories.business_activation_knowledge_repository import BusinessActivationKnowledgeRepository
from repositories.business_discovery_repository import BusinessDiscoveryRepository
from services.business_activation_extraction import extract_business_facts
from services.business_activation_knowledge import BusinessActivationKnowledgeService

# R13 starts conservatively. It can grow from deterministic planning into a bounded
# model-assisted planner without creating another Knowledge or memory subsystem.
DEFAULT_DISCOVERY_ORDER = [
    FactType.BUSINESS_IDENTITY, FactType.BUSINESS_DESCRIPTION, FactType.PRODUCT_SERVICE,
    FactType.LOCATION, FactType.BUSINESS_HOURS, FactType.CONTACT_CHANNEL,
    FactType.CUSTOMER_SEGMENT, FactType.PRICING, FactType.PAYMENT_METHOD,
    FactType.FULFILLMENT, FactType.DELIVERY_AREA, FactType.REFUND_POLICY,
]

class BusinessDiscoveryService:
    def __init__(self, repository: BusinessDiscoveryRepository, knowledge: BusinessActivationKnowledgeService | None = None):
        self.repository = repository
        self.knowledge = knowledge or BusinessActivationKnowledgeService(BusinessActivationKnowledgeRepository())
        self.planner = BusinessDiscoveryPlanner()

    def _next_gap(self, session: DiscoverySession) -> DiscoveryGap | None:
        confirmed = set(session.completed_fact_types)
        requested = session.gaps
        scope = session.requested_scope or DEFAULT_DISCOVERY_ORDER
        # Explicit requested scope is encoded in unresolved gaps; otherwise use the
        # canonical activation order and only ask for the first missing capability.
        for fact_type in scope:
            if fact_type in confirmed:
                continue
            if any(g.fact_type == fact_type and not g.resolved for g in requested):
                continue
            return DiscoveryGap(
                fact_type=fact_type,
                reason=f'KDO needs confirmed business knowledge for {fact_type.value}.',
                requested_evidence=['Explain it here', 'Provide a website link', 'Provide a document'],
            )
        return None

    def _question(self, gap: DiscoveryGap) -> str:
        prompts = {
            FactType.BUSINESS_IDENTITY: 'What is the business name and how should KDO identify it?',
            FactType.BUSINESS_DESCRIPTION: 'What does the business do? You can explain it here or provide your website/documentation.',
            FactType.PRODUCT_SERVICE: 'What products or services do you provide? A catalogue, website or document is also acceptable.',
            FactType.LOCATION: 'Where does the business operate? Include branches or service locations if relevant.',
            FactType.BUSINESS_HOURS: 'What are your operating hours? You can also provide a document or webpage containing them.',
            FactType.CONTACT_CHANNEL: 'Which customer contact channels should KDO know about (phone, WhatsApp, email, etc.)?',
            FactType.CUSTOMER_SEGMENT: 'Who do you serve? Describe your main customer groups.',
            FactType.PRICING: 'Where can KDO find your current pricing, fees or discounts?',
            FactType.PAYMENT_METHOD: 'Which payment methods do you accept?',
            FactType.FULFILLMENT: 'How are products or services fulfilled, delivered, picked up or dispatched?',
            FactType.DELIVERY_AREA: 'Where do you deliver or provide service?',
            FactType.REFUND_POLICY: 'What are your refund, return or exchange rules? A policy document or webpage is welcome.',
        }
        return prompts.get(gap.fact_type, f'Please provide confirmed information about {gap.fact_type.value}.')

    def _refresh_completion(self, session: DiscoverySession) -> None:
        facts = []
        scope = session.requested_scope or DEFAULT_DISCOVERY_ORDER
        for fact_type in scope:
            facts.extend(self.knowledge.current(__import__('domain.business_activation.facts', fromlist=['CurrentFactsQuery']).CurrentFactsQuery(
                workspace_id=session.workspace_id, fact_type=fact_type, limit=1
            )))
        session.completed_fact_types = list(dict.fromkeys([f.fact_type for f in facts]))

    def start(self, req: StartDiscoveryRequest) -> DiscoverySession:
        planned_scope, planner_reasons = self.planner.infer(req.business_description, req.requested_scope)
        session = DiscoverySession(workspace_id=req.workspace_id, requested_scope=planned_scope)
        session.planner_reasons = planner_reasons
        session.detected_profiles = [r.split(':', 2)[1] for r in planner_reasons if r.startswith('profile:')]
        session.turns.append(DiscoveryTurn(role='business', text=req.business_description))
        extraction = extract_business_facts(ExtractionRequest(
            workspace_id=req.workspace_id,
            operator_text=req.business_description,
            fact_types_in_scope=planned_scope,
        ))
        # Existing extraction remains reviewable. R13 does not auto-confirm it.
        self.knowledge.record_extraction(extraction, req.business_description, req.workspace_id, [item.value for item in planned_scope])
        session.pending_extraction_id = extraction.extraction_id
        session.pending_fact_ids = [fact.fact_id for fact in extraction.proposed_facts]
        session.turns.append(DiscoveryTurn(role='system', text=f'Business model analyzed; discovery scope contains {len(planned_scope)} knowledge requirements and {len(extraction.proposed_facts)} candidate facts were identified.', extraction_id=extraction.extraction_id))
        gap = self._next_gap(session)
        if gap:
            session.gaps.append(gap)
            session.current_gap_id = gap.gap_id
            session.turns.append(DiscoveryTurn(role='agent', text=self._question(gap)))
        session.updated_at = datetime.now(timezone.utc)
        return self.repository.save(session)

    def continue_session(self, req: ContinueDiscoveryRequest, asserted_by: str = 'authenticated-principal') -> DiscoverySession:
        session = self.repository.get(req.workspace_id, req.session_id)
        if not session:
            raise ValueError('discovery_session_not_found')
        session.turns.append(DiscoveryTurn(role='business', text=req.message, confirmed_fact_ids=req.confirm_fact_ids))

        confirm_ids = list(req.confirm_fact_ids)
        if req.confirm_pending and session.pending_fact_ids:
            confirm_ids = list(session.pending_fact_ids)

        if confirm_ids:
            if not session.pending_extraction_id or not set(confirm_ids).issubset(set(session.pending_fact_ids)):
                raise ValueError('pending_fact_not_found')
            facts, _, _ = __import__('asyncio').run(self.knowledge.confirm(
                req.workspace_id, session.pending_extraction_id, confirm_ids, asserted_by
            ))
            session.turns.append(DiscoveryTurn(role='system', text=f'{len(facts)} business facts confirmed into the existing Knowledge Plane.', confirmed_fact_ids=[f.fact_id for f in facts]))
            session.pending_extraction_id = None
            session.pending_fact_ids = []

        # Every conversational response is routed through the existing extraction
        # boundary. Candidates are reviewable and remain unconfirmed until explicit
        # confirmation arrives in a later turn.
        gap = next((g for g in session.gaps if g.gap_id == session.current_gap_id), None)
        extraction_scope = [gap.fact_type] if gap else session.requested_scope
        if req.message and extraction_scope:
            extraction = extract_business_facts(ExtractionRequest(
                workspace_id=req.workspace_id, operator_text=req.message, fact_types_in_scope=extraction_scope
            ))
            if extraction.proposed_facts:
                self.knowledge.record_extraction(extraction, req.message, req.workspace_id, [item.value for item in extraction_scope])
                session.pending_extraction_id = extraction.extraction_id
                session.pending_fact_ids = [fact.fact_id for fact in extraction.proposed_facts]
                session.turns.append(DiscoveryTurn(role='system', text=f'{len(extraction.proposed_facts)} candidate facts identified for review.', extraction_id=extraction.extraction_id))

        self._refresh_completion(session)
        if gap and any(g.gap_id == gap.gap_id for g in session.gaps):
            gap.resolved = gap.fact_type in session.completed_fact_types
        next_gap = self._next_gap(session)
        if next_gap:
            session.gaps.append(next_gap)
            session.current_gap_id = next_gap.gap_id
            session.status = DiscoveryStatus.ACTIVE
            session.turns.append(DiscoveryTurn(role='agent', text=self._question(next_gap)))
        else:
            session.current_gap_id = None
            session.status = DiscoveryStatus.READY
            session.turns.append(DiscoveryTurn(role='agent', text='The current activation discovery scope is complete. KDO can now continue with the configured business model or identify additional sector-specific requirements.'))
        session.updated_at = datetime.now(timezone.utc)
        return self.repository.save(session)
