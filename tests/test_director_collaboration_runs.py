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


def test_collaboration_run_is_completed_only_when_all_peers_and_sources_complete():
    assert _collaboration_run_status([
        {"status": "completed", "sources": {"partial": False}},
        {"status": "completed", "sources": {"partial": False}},
    ]) == "completed"
