from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.provider_closure_planner import plan_provider_closure
from services.sector_provider_gap_requirements import GAPS


def _candidate(action, *, semantic=True, active=True, complete=True):
    gap = next(g for g in GAPS if g["sector_action"] == action)
    return {
        "sector_action": action,
        "provider": "example",
        "governed_provider_action": "example.execute",
        "semantic_match": semantic,
        "provider_capability": gap["required_capability"],
        "provider_receipt": "receipt" if complete else "",
        "authoritative_verification": "authoritative lookup" if complete else "",
        "reconciliation": "durable lookup" if complete else "",
        "identity_mapping": "mapping" if complete else "",
        "certification_status": "ACTIVE" if active else "REGISTERED",
        "satisfied_gates": gap["minimum_binding_gates"] if complete else (),
    }


def test_all_frozen_gaps_are_evaluated():
    result = plan_provider_closure()
    assert result["summary"]["gaps"] == 8
    assert result["summary"]["no_candidate"] == 8


def test_complete_candidate_is_only_ready_for_existing_binding_review():
    action = GAPS[0]["sector_action"]
    result = plan_provider_closure(candidates=[_candidate(action)])
    assessment = next(a for a in result["assessments"] if a["sector_action"] == action)
    assert assessment["state"] == "READY_FOR_EXISTING_BINDING_REVIEW"
    assert assessment["candidates"][0]["status"] == "CLOSED"


def test_partial_candidate_keeps_gap_open():
    action = GAPS[1]["sector_action"]
    result = plan_provider_closure(candidates=[_candidate(action, complete=False)])
    assessment = next(a for a in result["assessments"] if a["sector_action"] == action)
    assert assessment["state"] == "PROVIDER_GAP_REMAINS"
    assert assessment["candidates"][0]["status"] == "CANDIDATE"
    assert assessment["candidates"][0]["missing_proofs"]


def test_semantic_mismatch_never_closes_gap():
    action = GAPS[2]["sector_action"]
    result = plan_provider_closure(candidates=[_candidate(action, semantic=False)])
    assessment = next(a for a in result["assessments"] if a["sector_action"] == action)
    assert assessment["state"] == "NO_SEMANTIC_MATCH"


def test_inactive_provider_cannot_close_gap():
    action = GAPS[3]["sector_action"]
    result = plan_provider_closure(candidates=[_candidate(action, active=False)])
    assessment = next(a for a in result["assessments"] if a["sector_action"] == action)
    assert assessment["state"] == "PROVIDER_GAP_REMAINS"
    assert "ACTIVE_CERTIFIED_PROVIDER" in assessment["candidates"][0]["missing_proofs"]


def test_unknown_actions_are_ignored():
    result = plan_provider_closure(candidates=[{"sector_action": "unknown.action", "semantic_match": True}])
    assert result["summary"]["gaps"] == 8
    assert all(not a["candidates"] for a in result["assessments"])


def test_surface_is_non_executing():
    result = plan_provider_closure()
    safety = result["safety"]
    assert all(safety.values())
