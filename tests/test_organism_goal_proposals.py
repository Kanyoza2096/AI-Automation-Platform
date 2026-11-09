from datetime import datetime, timedelta, timezone

import pytest

from organism.goals import GoalPriority, GoalProposal, derive_goal_proposal
from organism.perception import Observation, ObservationKind


NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


def observation(**overrides):
    values = {
        "workspace_id": "kdo-engineering-staging",
        "kind": ObservationKind.HEALTH,
        "source": "unified_event:service_heartbeat",
        "value": {"service": "api-worker", "status": "degraded", "health": 0.4},
        "confidence": 0.8,
        "source_trust": 0.8,
        "observation_id": "event:evt-health-001",
        "metadata": {"event_id": "evt-health-001"},
    }
    values.update(overrides)
    return Observation(**values)


def test_degraded_service_event_creates_bounded_suggest_only_goal_proposal():
    proposal = derive_goal_proposal(observation(), now=NOW)

    assert proposal is not None
    assert proposal.workspace_id == "kdo-engineering-staging"
    assert proposal.source_event_ids == ("evt-health-001",)
    assert proposal.title == "Restore health of api-worker"
    assert proposal.priority == GoalPriority.HIGH
    assert proposal.risk_level == "medium"
    assert proposal.authority == "suggest"
    assert proposal.status == "proposed"
    assert proposal.created_at == NOW
    assert proposal.expires_at == NOW + timedelta(hours=1)


def test_proposal_id_is_deterministic_for_replayed_event():
    first = derive_goal_proposal(observation(), now=NOW)
    second = derive_goal_proposal(observation(), now=NOW + timedelta(minutes=1))

    assert first.proposal_id == second.proposal_id
    assert first.source_event_ids == second.source_event_ids


@pytest.mark.parametrize("status", ["healthy", "ok", ""])
def test_healthy_or_unspecified_status_does_not_create_goal(status):
    assert derive_goal_proposal(
        observation(value={"service": "api-worker", "status": status}),
        now=NOW,
    ) is None


def test_only_allowlisted_health_event_sources_can_create_proposals():
    assert derive_goal_proposal(observation(source="unified_event:task_failed"), now=NOW) is None
    assert derive_goal_proposal(observation(kind=ObservationKind.EXECUTION), now=NOW) is None
    assert derive_goal_proposal(observation(source="manual_input"), now=NOW) is None


def test_proposal_requires_trusted_evidence_and_event_identity():
    assert derive_goal_proposal(observation(confidence=0.5), now=NOW) is None
    assert derive_goal_proposal(observation(source_trust=0.5), now=NOW) is None
    assert derive_goal_proposal(observation(metadata={}), now=NOW) is None


def test_proposal_is_tenant_scoped_and_service_label_is_sanitized():
    proposal = derive_goal_proposal(
        observation(
            workspace_id="workspace-b",
            value={"service": "api worker / production", "status": "down"},
        ),
        now=NOW,
    )

    assert proposal.workspace_id == "workspace-b"
    assert proposal.title == "Restore health of api-worker-production"
    assert proposal.priority == GoalPriority.CRITICAL
    assert proposal.risk_level == "high"


def test_goal_proposal_rejects_execution_authority_and_expired_window():
    base = dict(
        proposal_id="gprop_test",
        workspace_id="workspace-a",
        title="Restore service",
        success_condition="service healthy",
        source_event_ids=("evt-1",),
        priority=GoalPriority.HIGH,
        risk_level="medium",
        expected_outcome="service healthy",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )
    with pytest.raises(ValueError, match="suggest-only"):
        GoalProposal(**base, authority="execute")
    with pytest.raises(ValueError, match="later than created_at"):
        GoalProposal(**{**base, "expires_at": NOW})


def connector_health_observation(*, event_id="event-a", evidence_id="evidence-a", **metadata_overrides):
    metadata = {
        "event_id": event_id,
        "producer_id": "kdo-tenant-health-facebook",
        "producer_class": "tenant_connector_health",
        "provenance_verification": "ed25519_v1",
        "brand_id": "brand-a",
        "connection_id": "connection-a",
        "social_account_id": "account-a",
        "evidence_id": evidence_id,
        "evidence_hash": "b" * 64,
    }
    metadata.update(metadata_overrides)
    return observation(
        source="unified_event:connector.health.observed",
        value={
            "service": "facebook:connection-a",
            "status": "degraded",
            "health_state": "degraded",
            "connector_key": "facebook",
        },
        confidence=0.9,
        source_trust=0.9,
        observation_id=f"event:{event_id}",
        metadata=metadata,
    )


def test_signed_tenant_facebook_health_evidence_can_create_suggest_only_goal():
    proposal = derive_goal_proposal(connector_health_observation(), now=NOW)

    assert proposal is not None
    assert proposal.workspace_id == "kdo-engineering-staging"
    assert proposal.source_event_ids == ("event-a",)
    assert proposal.title == "Restore health of facebook:connection-a"
    assert proposal.authority == "suggest"
    assert proposal.status == "proposed"


def test_facebook_health_proposal_is_idempotent_across_event_publish_retries():
    first = derive_goal_proposal(
        connector_health_observation(event_id="event-first"), now=NOW
    )
    retry = derive_goal_proposal(
        connector_health_observation(event_id="event-retry"), now=NOW + timedelta(minutes=1)
    )

    assert first is not None and retry is not None
    assert first.proposal_id == retry.proposal_id
    assert first.source_event_ids == ("event-first",)
    assert retry.source_event_ids == ("event-retry",)


@pytest.mark.parametrize("overrides", [
    {"producer_id": "caller-controlled"},
    {"producer_class": "platform_monitor"},
    {"provenance_verification": None},
    {"brand_id": None},
    {"connection_id": None},
    {"evidence_id": ""},
    {"evidence_hash": "bad"},
])
def test_facebook_health_goal_requires_verified_producer_and_durable_evidence(overrides):
    assert derive_goal_proposal(
        connector_health_observation(**overrides), now=NOW
    ) is None


def test_unknown_facebook_health_state_does_not_create_recovery_goal():
    item = connector_health_observation()
    item = observation(
        source=item.source,
        value={**item.value, "status": "unknown", "health_state": "unknown"},
        confidence=item.confidence,
        source_trust=item.source_trust,
        observation_id=item.observation_id,
        metadata=item.metadata,
    )

    assert derive_goal_proposal(item, now=NOW) is None
