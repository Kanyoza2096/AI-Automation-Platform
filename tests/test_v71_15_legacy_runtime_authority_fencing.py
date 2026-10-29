import asyncio
from types import SimpleNamespace

from services.v71_15_legacy_runtime_authority_fencing import (
    AUTHORITY_MAP, LEGACY_DIRECT_SIDE_EFFECTS, LegacyRuntimeAuthorityFence,
)


def test_authority_map_has_single_canonical_executor():
    executor = [x.component for x in AUTHORITY_MAP if "execute" in {m.value for m in x.modes}]
    assert "Action Kernel" in executor
    assert "V69 Connector Runtime Gateway" in executor
    assert len([x for x in executor if x in {"Director", "AutonomousLoop", "Schedulers"}]) == 0


def test_legacy_side_effects_are_fenced_closed():
    fence = LegacyRuntimeAuthorityFence(production=True)
    for capability in LEGACY_DIRECT_SIDE_EFFECTS:
        assert fence.allows_legacy_execution(capability) is False
        result = fence.fence_result(component="Director", capability=capability)
        assert result["status"] == "fenced"
        assert result["authority_granted"] is False
        assert result["execution_requested"] is False


def test_fence_status_is_observable():
    fence = LegacyRuntimeAuthorityFence(production=True)
    status = fence.status()
    assert status["version"] == "v71.15"
    assert status["production_fence"] is True
    assert status["parallel_authority_plane"] is False
    assert status["binding_fingerprint"]


def test_production_fence_preserves_non_consequential_director_diagnostics():
    fence = LegacyRuntimeAuthorityFence(production=True)
    assert fence.allows_legacy_execution("calculate_brand_health") is True
    assert fence.allows_legacy_execution("run_health_scan") is True
    assert fence.allows_legacy_execution("publish_scheduled_content") is False
