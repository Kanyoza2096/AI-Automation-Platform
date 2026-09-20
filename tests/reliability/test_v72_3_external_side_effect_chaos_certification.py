from services.v72_3_external_side_effect_chaos_certification import run_c83_certification


def test_c83_external_side_effect_chaos_certification_passes():
    report = run_c83_certification()
    assert report["status"] == "PASS"
    assert report["passed"] == 5
    assert report["failed"] == 0
    assert report["production_provider_tested"] is False
    assert all(item["passed"] for item in report["scenarios"])


def test_c83_duplicate_delivery_has_one_remote_object():
    report = run_c83_certification()
    duplicate = next(x for x in report["scenarios"] if x["name"] == "concurrent_duplicate_delivery")
    assert duplicate["evidence"]["provider_calls"] == 2
    assert duplicate["evidence"]["committed"] == 1
