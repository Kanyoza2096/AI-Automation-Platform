import pytest

from services.v73_objective_aware_workforce_execution import (
    build_objective_execution_context,
    validate_objective_execution_context,
    assert_execution_lineage,
)


def objective():
    return {
        "schema_version": "1.0",
        "objective_id": "obj_growth",
        "workspace_id": "ws1",
        "objective": "Increase qualified customer conversions",
        "state": "active",
        "context_fingerprint": "fp_obj",
        "authority": "none",
    }


def test_execution_context_is_bound_to_objective_and_run():
    ctx = build_objective_execution_context(
        workspace_id="ws1", run_id="run1", objective_context=objective(),
        success_criteria={"qualified_conversations": {"operator": ">=", "target": 5}},
    )
    validate_objective_execution_context(ctx, workspace_id="ws1", run_id="run1")
    assert ctx["objective_id"] == "obj_growth"
    assert ctx["authority"] == "none"


def test_cross_workspace_execution_context_rejected():
    ctx = build_objective_execution_context(workspace_id="ws1", run_id="run1", objective_context=objective())
    with pytest.raises(ValueError, match="workspace mismatch"):
        validate_objective_execution_context(ctx, workspace_id="ws2", run_id="run1")


def test_run_objective_lineage_must_match():
    ctx = build_objective_execution_context(workspace_id="ws1", run_id="run1", objective_context=objective())
    run = {"workspace_id": "ws1", "run_id": "run1", "objective_id": "obj_other"}
    with pytest.raises(ValueError, match="lineage mismatch"):
        assert_execution_lineage(run=run, execution_context=ctx)


def test_execution_context_cannot_grant_authority():
    ctx = build_objective_execution_context(workspace_id="ws1", run_id="run1", objective_context=objective())
    ctx["authority"] = "execute"
    with pytest.raises(ValueError, match="cannot grant authority"):
        validate_objective_execution_context(ctx, workspace_id="ws1", run_id="run1")


def test_durable_workforce_requires_objective_context_before_execution():
    from types import SimpleNamespace
    from services.v71_17_durable_workforce_execution import DurableWorkforceExecutionCoordinator, WorkforceExecutionBlock
    run = SimpleNamespace(
        workspace_id="ws1", objective="Increase qualified customer conversions",
        event={"_kdo_governance_envelope": {"objective_id": "obj_growth"}},
    )
    manifest = run.event["_kdo_governance_envelope"]
    with pytest.raises(ValueError, match=WorkforceExecutionBlock.OBJECTIVE_BINDING_REQUIRED.value):
        DurableWorkforceExecutionCoordinator._validate_objective_binding(run, manifest, "ws1")


def test_durable_workforce_rejects_tampered_objective_manifest():
    from types import SimpleNamespace
    from services.v71_17_durable_workforce_execution import DurableWorkforceExecutionCoordinator, WorkforceExecutionBlock
    ctx = objective()
    run = SimpleNamespace(
        workspace_id="ws1", objective=ctx["objective"],
        event={"objective_id": "obj_growth", "_kdo_objective_context": ctx},
    )
    with pytest.raises(ValueError, match=WorkforceExecutionBlock.OBJECTIVE_BINDING_INVALID.value):
        DurableWorkforceExecutionCoordinator._validate_objective_binding(
            run, {"objective_id": "obj_other"}, "ws1"
        )
