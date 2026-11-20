import pytest
from fastapi import HTTPException

from api.routers.ai_chat import (
    _collaboration_run_status,
    _normalize_collaboration_engines,
)


def test_collaboration_engine_selection_is_bounded_and_deduplicated():
    assert _normalize_collaboration_engines(
        ["guardian", "ai_brain", "guardian"]
    ) == ["guardian", "ai_brain"]


def test_collaboration_engine_selection_rejects_unapproved_engines():
    with pytest.raises(HTTPException) as exc:
        _normalize_collaboration_engines(["director"])
    assert exc.value.status_code == 422


def test_collaboration_engine_selection_rejects_more_than_four_engines():
    with pytest.raises(HTTPException) as exc:
        _normalize_collaboration_engines(
            ["guardian", "ai_brain", "council", "content", "guardian"]
        )
    assert exc.value.status_code == 422


def test_collaboration_run_is_partial_when_peer_is_unavailable():
    assert _collaboration_run_status([
        {"status": "completed", "sources": {"partial": False}},
        {"status": "unavailable", "sources": {"partial": False}},
    ]) == "partial"


def test_collaboration_run_is_partial_when_evidence_source_is_unavailable():
    assert _collaboration_run_status([
        {"status": "completed", "sources": {"partial": True}},
    ]) == "partial"


def test_collaboration_run_is_completed_only_when_all_outputs_are_available():
    peers = [
        {"status": "completed", "sources": {"partial": False}},
        {"status": "completed", "sources": {"partial": False}},
    ]
    comparison = {"status": "available"}
    recommendation = {"status": "available"}
    assert _collaboration_run_status(peers, comparison, recommendation) == "completed"


def test_collaboration_run_is_partial_when_comparison_or_recommendation_is_unavailable():
    peers = [{"status": "completed", "sources": {"partial": False}}]
    assert _collaboration_run_status(
        peers, {"status": "unavailable"}, {"status": "available"}
    ) == "partial"
    assert _collaboration_run_status(
        peers, {"status": "available"}, {"status": "unavailable"}
    ) == "partial"
