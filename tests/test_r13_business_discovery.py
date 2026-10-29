from domain.business_activation.facts import FactType
from domain.business_discovery.discovery import StartDiscoveryRequest, ContinueDiscoveryRequest
from repositories.business_discovery_repository import BusinessDiscoveryRepository
from repositories.business_activation_knowledge_repository import BusinessActivationKnowledgeRepository
from services.business_activation_knowledge import BusinessActivationKnowledgeService
from services.business_discovery import BusinessDiscoveryService

def service():
    return BusinessDiscoveryService(
        BusinessDiscoveryRepository(),
        BusinessActivationKnowledgeService(BusinessActivationKnowledgeRepository()),
    )

def test_r13_starts_conversation_without_auto_confirming_knowledge():
    s = service().start(StartDiscoveryRequest(workspace_id='ws-1', business_description='We sell locally made sofas.'))
    assert s.status.value == 'ACTIVE'
    assert any(t.role == 'agent' for t in s.turns)
    assert all(not f for f in [s.completed_fact_types])

def test_r13_remembers_session_and_can_progress_after_confirmation():
    svc = service()
    s = svc.start(StartDiscoveryRequest(workspace_id='ws-1', business_description='We are open Monday-Friday 9:00-17:00 EAT.', requested_scope=[FactType.BUSINESS_HOURS]))
    assert s.pending_extraction_id
    assert s.pending_fact_ids
    s2 = svc.continue_session(ContinueDiscoveryRequest(
        workspace_id='ws-1', session_id=s.session_id, message='Yes, that is correct.', confirm_pending=True
    ), asserted_by='operator@example.com')
    assert FactType.BUSINESS_HOURS in s2.completed_fact_types
    assert any('confirmed into the existing Knowledge Plane' in t.text for t in s2.turns)

def test_r13_workspace_isolation():
    svc = service()
    s = svc.start(StartDiscoveryRequest(workspace_id='ws-1', business_description='We provide delivery services.'))
    try:
        svc.continue_session(ContinueDiscoveryRequest(workspace_id='ws-2', session_id=s.session_id, message='hello'))
        assert False
    except ValueError as exc:
        assert str(exc) == 'discovery_session_not_found'

def test_r13_planner_expands_scope_from_business_signals_without_auto_confirmation():
    svc = service()
    s = svc.start(StartDiscoveryRequest(
        workspace_id='ws-planner',
        business_description='We run a delivery and courier business with online payments and shipping.'
    ))
    assert FactType.DELIVERY_AREA in s.requested_scope
    assert FactType.FULFILLMENT in s.requested_scope
    assert FactType.PAYMENT_METHOD in s.requested_scope
    assert 'logistics' in s.detected_profiles
    assert all(not f for f in s.completed_fact_types)


def test_r13_explicit_scope_overrides_planner():
    svc = service()
    s = svc.start(StartDiscoveryRequest(
        workspace_id='ws-explicit',
        business_description='We run a restaurant and delivery service.',
        requested_scope=[FactType.BUSINESS_HOURS]
    ))
    assert s.requested_scope == [FactType.BUSINESS_HOURS]
    assert s.planner_reasons == ['explicit_scope']
