from services.v72_2_database_failure_certification import DatabaseFailureCertification


def test_c82_database_failure_certification_passes_all_invariants():
    report = DatabaseFailureCertification().run()
    assert report.certification == "PASS"
    assert report.passed == 6
    assert report.failed == 0


def test_c82_ambiguity_never_becomes_blind_retry():
    report = DatabaseFailureCertification().run()
    ambiguous = {x.point: x for x in report.scenarios}
    for point in ("after_commit_before_receipt", "after_side_effect_before_commit", "receipt_persistence_failure", "stale_lease"):
        assert ambiguous[point].passed
        assert ambiguous[point].evidence["retry_allowed"] is False
