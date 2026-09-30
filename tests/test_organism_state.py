from datetime import datetime, timezone

import pytest

from core.organism_state import Confidence, OperatingMode, OrganismState


def test_state_is_bounded_and_json_friendly():
    state = OrganismState(
        workspace_id="workspace-1",
        mode=OperatingMode.PLANNING,
        health=0.9,
        goal_progress=0.25,
        confidence=Confidence.HIGH,
        objective="increase engagement",
        source_versions={"meta": "14.7.5"},
    )
    payload = state.to_dict()
    assert payload["workspace_id"] == "workspace-1"
    assert payload["mode"] == "planning"
    assert payload["confidence"] == "high"
    assert payload["source_versions"] == {"meta": "14.7.5"}
    assert payload["updated_at"].endswith("+00:00")


def test_state_rejects_out_of_range_metrics():
    with pytest.raises(ValueError, match="health"):
        OrganismState(workspace_id="ws", health=1.1)


def test_state_requires_timezone_aware_timestamp():
    with pytest.raises(ValueError, match="timezone-aware"):
        OrganismState(workspace_id="ws", updated_at=datetime.now())


def test_state_round_trip():
    original = OrganismState(
        workspace_id="ws",
        mode=OperatingMode.EXECUTING,
        confidence=Confidence.LOW,
        risk=0.7,
        updated_at=datetime(2026, 9, 5, 20, 0, tzinfo=timezone.utc),
    )
    restored = OrganismState.from_mapping(original.to_dict())
    assert restored == original
