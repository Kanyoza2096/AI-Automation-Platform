from services.v72_2_command_center_read_model import CommandCenterReadModel


def test_read_model_has_all_canonical_sections():
    model = CommandCenterReadModel("ws-1").build({})
    assert model["schema"] == "CommandCenterReadModel/v1"
    assert set(model["sections"]) == {
        "businessHealth", "objectives", "opportunities", "risks", "decisions",
        "workforce", "operations", "outcomes", "evidence", "governance",
    }


def test_missing_truth_is_honest_and_not_fabricated():
    model = CommandCenterReadModel("ws-1").build({})
    assert model["state"] == "UNKNOWN"
    assert all(v["state"] == "UNKNOWN" for v in model["sections"].values())
    assert all(v["actionAuthorityGranted"] is False for v in model["sections"].values())


def test_authoritative_snapshot_carries_evidence():
    model = CommandCenterReadModel("ws-1").build({"workforce": {"healthy": True}})
    workforce = model["sections"]["workforce"]
    assert workforce["state"] == "LIVE"
    assert workforce["data"] == {"healthy": True}
    assert workforce["evidence"]
    assert len(workforce["evidence"][0]["fingerprint"]) == 64


def test_read_model_cannot_grant_action_authority():
    model = CommandCenterReadModel("ws-1").build({"governance": {"state": "LIVE"}})
    assert model["authority"]["actionAuthorityGranted"] is False
    assert model["sections"]["governance"]["actionAuthorityGranted"] is False
