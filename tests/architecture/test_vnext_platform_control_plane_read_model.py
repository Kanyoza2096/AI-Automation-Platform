from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def test_platform_control_plane_has_ten_canonical_sections():
    from services.vnext_platform_control_plane_read_model import SECTIONS
    assert SECTIONS == (
        "situation", "intelligence", "governance", "execution", "connectorFabric",
        "sectorPacks", "workforce", "observability", "securityTenancy", "certification",
    )


def test_platform_control_plane_is_read_only_and_does_not_grant_authority():
    from services.vnext_platform_control_plane_read_model import build_platform_control_plane_read_model
    result = build_platform_control_plane_read_model("ws-test", db=None, runtime=None)
    assert result["authority"]["readOnly"] is True
    assert result["authority"]["actionAuthorityGranted"] is False
    assert result["sections"]["connectorFabric"]["data"]["activationRegistry"]["state"] == "NOT_EXPOSED"
    assert result["sections"]["connectorFabric"]["data"]["certificationRegistry"]["state"] == "NOT_EXPOSED"
    assert all(section["actionAuthorityGranted"] is False for section in result["sections"].values())


def test_platform_control_plane_route_exists_and_is_get_only():
    source = (ROOT / "routes" / "command_center_routes.py").read_text(encoding="utf-8")
    assert '@router.get("/platform-control-plane"' in source
    route_block = source.split('@router.get("/platform-control-plane"', 1)[1].split('@router.get(', 1)[0]
    assert 'post(' not in route_block.lower()
    assert 'put(' not in route_block.lower()
    assert 'delete(' not in route_block.lower()
