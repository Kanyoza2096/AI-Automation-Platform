import pytest

from services.phase9_disaster_recovery import (
    RecoveryArtifact,
    RecoveryArtifactType,
    RecoveryObjective,
    RecoveryPlan,
    RecoveryReadiness,
    RecoveryStep,
    RecoveryStepKind,
    RecoveryVerificationStatus,
    assess_recovery,
    continuity_window_met,
    validate_recovery_plan,
)


def objective(**kw):
    data = dict(
        workspace_id="ws_1",
        resource_ref="platform:core",
        rto_seconds=3600,
        rpo_seconds=900,
    )
    data.update(kw)
    return RecoveryObjective(**data)


def artifact(kind, verification=RecoveryVerificationStatus.VERIFIED, **kw):
    data = dict(
        workspace_id="ws_1",
        resource_ref="platform:core",
        artifact_type=kind,
        evidence_ref="ev_1",
        created_at="2026-10-05T08:00:00Z",
        verification=verification,
    )
    data.update(kw)
    return RecoveryArtifact(**data)


def test_verified_backup_and_configuration_are_ready():
    a = assess_recovery(
        objective(),
        (
            artifact(RecoveryArtifactType.BACKUP),
            artifact(RecoveryArtifactType.CONFIGURATION),
        ),
    )
    assert a.readiness == RecoveryReadiness.READY
    assert a.can_resume


def test_missing_recovery_artifacts_are_unknown():
    a = assess_recovery(objective(), ())
    assert a.readiness == RecoveryReadiness.UNKNOWN
    assert not a.can_resume


def test_partial_evidence_is_degraded():
    a = assess_recovery(
        objective(),
        (artifact(RecoveryArtifactType.BACKUP),),
    )
    assert a.readiness == RecoveryReadiness.DEGRADED
    assert not a.can_resume


def test_failed_artifact_blocks_recovery():
    a = assess_recovery(
        objective(),
        (
            artifact(RecoveryArtifactType.BACKUP),
            artifact(
                RecoveryArtifactType.CONFIGURATION,
                RecoveryVerificationStatus.FAILED,
            ),
        ),
    )
    assert a.readiness == RecoveryReadiness.BLOCKED
    assert not a.can_resume
    assert len(a.failed_artifacts) == 1


def test_workspace_isolation():
    with pytest.raises(ValueError):
        assess_recovery(
            objective(),
            (artifact(RecoveryArtifactType.BACKUP, workspace_id="ws_2"),),
        )


def test_resource_isolation():
    with pytest.raises(ValueError):
        assess_recovery(
            objective(),
            (artifact(RecoveryArtifactType.BACKUP, resource_ref="platform:other"),),
        )


def test_recovery_objective_cannot_carry_authority():
    with pytest.raises(ValueError):
        objective(authority="executor")


def test_recovery_artifact_cannot_carry_authority():
    with pytest.raises(ValueError):
        artifact(RecoveryArtifactType.BACKUP, authority="executor")


def test_recovery_plan_order_is_deterministic():
    plan = RecoveryPlan(
        workspace_id="ws_1",
        name="core restore",
        steps=(
            RecoveryStep("s2", "db", RecoveryStepKind.VERIFY, 2),
            RecoveryStep("s1", "db", RecoveryStepKind.RESTORE, 1),
        ),
    )
    assert [x.step_id for x in plan.ordered_steps()] == ["s1", "s2"]


def test_duplicate_order_is_rejected():
    with pytest.raises(ValueError):
        RecoveryPlan(
            workspace_id="ws_1",
            name="invalid",
            steps=(
                RecoveryStep("s1", "db", RecoveryStepKind.RESTORE, 1),
                RecoveryStep("s2", "api", RecoveryStepKind.RESTORE, 1),
            ),
        )


def test_unverified_dependency_blocks_plan_readiness():
    plan = RecoveryPlan(
        workspace_id="ws_1",
        name="restore",
        steps=(
            RecoveryStep("s1", "db", RecoveryStepKind.RESTORE, 1),
            RecoveryStep("s2", "db", RecoveryStepKind.RECONCILE, 2),
        ),
    )
    assert not validate_recovery_plan(plan, verified_dependencies=())


def test_verified_dependency_allows_plan_validation():
    plan = RecoveryPlan(
        workspace_id="ws_1",
        name="restore",
        steps=(
            RecoveryStep("s1", "db", RecoveryStepKind.RESTORE, 1),
            RecoveryStep("s2", "db", RecoveryStepKind.RECONCILE, 2),
        ),
    )
    assert validate_recovery_plan(plan, verified_dependencies=("db",))


def test_rto_rpo_met():
    assert continuity_window_met(
        objective=objective(rto_seconds=100, rpo_seconds=20),
        elapsed_recovery_seconds=100,
        data_loss_seconds=20,
    )


def test_rto_or_rpo_breach_fails():
    assert not continuity_window_met(
        objective=objective(rto_seconds=100, rpo_seconds=20),
        elapsed_recovery_seconds=101,
        data_loss_seconds=0,
    )
    assert not continuity_window_met(
        objective=objective(rto_seconds=100, rpo_seconds=20),
        elapsed_recovery_seconds=90,
        data_loss_seconds=21,
    )


def test_negative_recovery_measurement_rejected():
    with pytest.raises(ValueError):
        continuity_window_met(
            objective=objective(),
            elapsed_recovery_seconds=-1,
            data_loss_seconds=0,
        )


def test_assessment_preserves_evidence_ids():
    a1 = artifact(RecoveryArtifactType.BACKUP)
    a2 = artifact(RecoveryArtifactType.CONFIGURATION)
    a = assess_recovery(objective(), (a1, a2))
    assert a.artifact_ids == (a1.artifact_id, a2.artifact_id)


def test_recovery_plan_cannot_carry_authority():
    with pytest.raises(ValueError):
        RecoveryPlan(
            workspace_id="ws_1",
            name="bad",
            steps=(),
            authority="executor",
        )


def test_assessment_is_not_authority():
    a = assess_recovery(objective(), ())
    with pytest.raises(ValueError):
        a.__class__(
            workspace_id="ws_1",
            resource_ref="platform:core",
            readiness=RecoveryReadiness.UNKNOWN,
            objective=a.objective,
            artifact_ids=(),
            failed_artifacts=(),
            verified_artifacts=(),
            authority="executor",
        )
