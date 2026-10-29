from services.business_automation_catalog import TEMPLATES
from services.v70_4_canonical_runtime import (
    CapabilityResolution,
    CanonicalActionIdentity,
    CanonicalEventEnvelope,
    CanonicalRuntimeReconciler,
    OutcomeLink,
)


def test_registered_capability_resolves_only_when_action_is_executable():
    resolver = CanonicalRuntimeReconciler(
        catalog=TEMPLATES,
        executable_actions={"crm_add_contact", "crm_schedule_followup", "notify_operator"},
    )
    result = resolver.resolve_capability("sales.new-lead-capture")
    assert result.status is CapabilityResolution.EXECUTABLE
    assert result.workflow_id == "sales.new-lead-capture"
    assert result.action == "crm_add_contact"


def test_missing_ai_recommendation_is_not_fabricated_into_execution():
    resolver = CanonicalRuntimeReconciler(catalog=TEMPLATES, executable_actions={"notify_operator"})
    result = resolver.resolve_capability("sales.high-intent-follow-up")
    assert result.status is CapabilityResolution.CAPABILITY_UNAVAILABLE
    assert not result.executable


def test_known_workflow_without_registered_action_is_advisory_only():
    resolver = CanonicalRuntimeReconciler(catalog=TEMPLATES, executable_actions=set())
    result = resolver.resolve_capability("sales.new-lead-capture")
    assert result.status is CapabilityResolution.ADVISORY_ONLY
    assert result.action == "crm_add_contact"


def test_canonical_action_identity_is_stable_for_replay():
    args = dict(
        workspace_id="ws_1",
        workflow_id="sales.new-lead-capture",
        action_type="crm_add_contact",
        event_id="evt_1",
        correlation_id="corr_1",
    )
    a = CanonicalActionIdentity.create(**args)
    b = CanonicalActionIdentity.create(**args)
    assert a.idempotency_key == b.idempotency_key
    assert a.workspace_id == "ws_1"


def test_canonical_event_envelope_requires_business_identity():
    envelope = CanonicalEventEnvelope(
        event_id="evt_1",
        workspace_id="ws_1",
        event_type="lead.created",
        source="website",
        payload={"name": "Ada"},
        correlation_id="corr_1",
    )
    envelope.validate()


def test_outcome_link_requires_the_full_business_value_chain():
    link = OutcomeLink(
        objective_id="obj_1",
        workflow_id="sales.new-lead-capture",
        action_id="act_1",
        outcome_type="lead_captured",
        evidence={"crm_contact_id": "c_1"},
    )
    link.validate()
