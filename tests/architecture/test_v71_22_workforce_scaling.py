from services.v71_22_workforce_scaling import WorkforceScalingCoordinator, ScalingDecision


def test_scaling_coordinator_is_advisory_only():
    names = dir(WorkforceScalingCoordinator)
    assert "decide" in names
    assert not any(x in " ".join(names).lower() for x in ("execute", "provision", "authorize"))


def test_scaling_decision_has_no_authority():
    fields = ScalingDecision.__dataclass_fields__
    assert "provisioning_authority_granted" in fields
    assert "execution_requested" in fields
