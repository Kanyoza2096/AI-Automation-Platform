from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.meta_agent_production_certification import (
    REQUIRED_BOUNDARIES,
    REQUIRED_STAGES,
    assess_meta_agent_certification,
)


def _passing_stages():
    return [{"stage": stage, "status": "PASS", "evidence": f"{stage}-evidence"} for stage in REQUIRED_STAGES]


def _passing_boundaries():
    return dict(REQUIRED_BOUNDARIES)


def _passing_safety():
    return {
        "no_silent_truth_promotion": True,
        "no_unauthorized_execution": True,
        "no_runtime_replacement": True,
        "no_action_kernel_bypass": True,
        "no_provider_activation": True,
    }


def test_complete_evidence_is_certification_candidate():
    result = assess_meta_agent_certification(
        stage_results=_passing_stages(),
        boundary_assertions=_passing_boundaries(),
        safety_assertions=_passing_safety(),
        integration_trace=[
            {"event": "knowledge_confirmation", "authority": "existing_knowledge_plane_confirmation"},
            {"event": "execution", "authority": "existing_action_kernel"},
        ],
    )
    assert result["assessment"] == "CERTIFICATION_CANDIDATE"
    assert result["blocking_reasons"] == []


def test_missing_stage_blocks_certification():
    stages = _passing_stages()[:-1]
    result = assess_meta_agent_certification(
        stage_results=stages,
        boundary_assertions=_passing_boundaries(),
        safety_assertions=_passing_safety(),
    )
    assert result["assessment"] == "BLOCKED"
    assert "MISSING_STAGE_EVIDENCE" in result["blocking_reasons"]
    assert "R13-I" in result["stage_summary"]["missing"]


def test_failed_stage_blocks_certification():
    stages = _passing_stages()
    stages[3]["status"] = "FAIL"
    result = assess_meta_agent_certification(
        stage_results=stages,
        boundary_assertions=_passing_boundaries(),
        safety_assertions=_passing_safety(),
    )
    assert result["assessment"] == "BLOCKED"
    assert "R13-D" in result["stage_summary"]["failed"]
    assert "FAILED_STAGE_EVIDENCE" in result["blocking_reasons"]


def test_boundary_bypass_blocks_certification():
    boundaries = _passing_boundaries()
    boundaries["action_authority"] = "meta_agent_direct_execution"
    result = assess_meta_agent_certification(
        stage_results=_passing_stages(),
        boundary_assertions=boundaries,
        safety_assertions=_passing_safety(),
    )
    assert result["assessment"] == "BLOCKED"
    assert "BOUNDARY_ASSERTION_FAILURE" in result["blocking_reasons"]


def test_forbidden_provider_bypass_blocks_certification():
    result = assess_meta_agent_certification(
        stage_results=_passing_stages(),
        boundary_assertions=_passing_boundaries(),
        safety_assertions=_passing_safety(),
        integration_trace=[{"event": "execute", "bypass": "direct_provider_execution"}],
    )
    assert result["assessment"] == "BLOCKED"
    assert "FORBIDDEN_ARCHITECTURAL_BYPASS" in result["blocking_reasons"]
    assert "direct_provider_execution" in result["safety_summary"]["observed_forbidden_bypasses"]


def test_failed_safety_assertion_blocks_certification():
    safety = _passing_safety()
    safety["no_silent_truth_promotion"] = False
    result = assess_meta_agent_certification(
        stage_results=_passing_stages(),
        boundary_assertions=_passing_boundaries(),
        safety_assertions=safety,
    )
    assert result["assessment"] == "BLOCKED"
    assert "SAFETY_ASSERTION_FAILURE" in result["blocking_reasons"]


def test_assessment_cannot_self_certify_production():
    result = assess_meta_agent_certification(
        stage_results=_passing_stages(),
        boundary_assertions=_passing_boundaries(),
        safety_assertions=_passing_safety(),
    )
    assert result["authority"]["this_assessment_can_certify_production"] is False
    assert result["authority"]["release_authority"] == "existing_production_certification_process"


def test_assessment_is_read_only_and_non_executing():
    result = assess_meta_agent_certification(stage_results=[])
    assert result["read_only"] is True
    assert all(result["safety"].values())
