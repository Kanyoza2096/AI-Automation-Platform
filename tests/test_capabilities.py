from organism.capabilities import (
    AuthorityRequirement,
    CapabilityHealth,
    CapabilityRegistry,
    CapabilityRisk,
    CapabilitySpec,
    CapabilityStatus,
)


def spec(workspace="w1", **kwargs):
    return CapabilitySpec(workspace, "publish", kwargs.pop("version", "1.0.0"), "publish content", **kwargs)


def test_register_and_resolve_is_tenant_scoped():
    registry = CapabilityRegistry()
    registry.register(spec("w1"))
    assert registry.get("w1", "publish") is not None
    assert registry.get("w2", "publish") is None


def test_authority_is_an_explicit_requirement():
    registry = CapabilityRegistry()
    registry.register(spec("w1", authority=AuthorityRequirement.EXECUTE))
    assert not registry.check("w1", "publish", authority="suggest").allowed
    assert registry.check("w1", "publish", authority="execute").allowed


def test_prerequisites_are_checked_before_use():
    registry = CapabilityRegistry()
    registry.register(spec("w1", prerequisites=("content_ready",)))
    denied = registry.check("w1", "publish", available=())
    assert not denied.allowed
    assert denied.missing_prerequisites == ("content_ready",)
    assert registry.check("w1", "publish", available=("content_ready",)).allowed


def test_quarantine_health_blocks_capability():
    registry = CapabilityRegistry()
    registry.register(spec("w1"))
    registry.set_health(CapabilityHealth("publish", "w1", CapabilityStatus.QUARANTINED, 0.0, "anomaly"))
    assert not registry.check("w1", "publish").allowed


def test_version_cannot_regress():
    registry = CapabilityRegistry()
    registry.register(spec("w1"))
    try:
        registry.register(spec("w1", version="0.9.0"))
        assert False
    except ValueError as exc:
        assert "backwards" in str(exc)


def test_capability_is_metadata_not_executable_authority():
    registry = CapabilityRegistry()
    registry.register(spec("w1", risk=CapabilityRisk.HIGH, metadata={"sector": "content"}))
    assert not hasattr(registry.get("w1", "publish"), "handler")
    assert registry.get("w1", "publish").risk is CapabilityRisk.HIGH


def test_forbidden_metadata_channels_are_rejected():
    try:
        spec("w1", metadata={"credentials": "never"})
        assert False
    except ValueError as exc:
        assert "credential" in str(exc)
