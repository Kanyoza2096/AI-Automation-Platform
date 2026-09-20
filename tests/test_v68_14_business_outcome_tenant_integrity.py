import pytest

from services.business_outcome_attribution import attribute_business_outcome
from services.v68_14_business_outcome_tenant_integrity import (
    assert_attribution_tenant_integrity,
    assert_observation_execution_workspace,
)


def observation(ws="ws-a", execution="exe-1"):
    return {
        "observation_id": "obs-1",
        "workspace_id": ws,
        "execution_id": execution,
        "observation_type": "business_outcome",
        "outcome_state": "positive",
        "source": "crm",
        "authority": "none",
    }


def test_observation_execution_must_share_workspace():
    with pytest.raises(ValueError, match="workspace mismatch"):
        assert_observation_execution_workspace(observation(), {"workspace_id": "ws-b", "execution_id": "exe-1"})


def test_observation_execution_id_must_match():
    with pytest.raises(ValueError, match="execution_id does not match"):
        assert_observation_execution_workspace(observation(), {"workspace_id": "ws-a", "execution_id": "exe-2"})


def test_attribution_ignores_cross_workspace_candidate():
    result = attribute_business_outcome(
        observation(),
        [
            {"workspace_id": "ws-b", "execution_id": "exe-1"},
            {"workspace_id": "ws-a", "execution_id": "exe-2"},
        ],
    )
    assert result["attribution_status"] == "unattributed"
    assert result["execution_id"] is None


def test_attribution_tenant_must_match_observation():
    with pytest.raises(ValueError, match="workspace mismatch"):
        assert_attribution_tenant_integrity(
            {"workspace_id": "ws-b", "observation_id": "obs-1"},
            observation(),
        )
