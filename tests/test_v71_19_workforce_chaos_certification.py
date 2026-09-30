from services.v71_19_workforce_chaos_certification import WorkforceChaosCertification


def test_v71_19_all_chaos_scenarios_pass_offline():
    report = WorkforceChaosCertification().run()
    assert report.passed == 10
    assert report.failed == 0
    assert report.certification == "PASS"
    assert len(report.fingerprint) == 64


def test_ambiguous_external_outcome_is_fenced():
    report = WorkforceChaosCertification().run()
    scenario = next(x for x in report.scenarios if x.scenario == "crash_after_side_effect")
    assert scenario.passed is True
    assert scenario.evidence["reconciliation_required"] is True


def test_certification_has_no_execution_authority():
    c = WorkforceChaosCertification()
    assert c.status()["provider_calls"] is False
    assert c.status()["parallel_execution_plane"] is False
    assert c.status()["authority_granted"] is False
