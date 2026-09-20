from services.v72_0_production_execution_certification import ProductionExecutionCertification


def test_v72_gate_never_upgrades_missing_postgres_to_pass():
    result = ProductionExecutionCertification().run()
    runtime = next(g for g in result["gates"] if g["name"] == "real_postgresql_concurrency")
    assert runtime["status"] in {"PASS", "NOT_PROVEN"}
    if not runtime["evidence"]["psql_available"] and not runtime["evidence"]["docker_available"]:
        assert runtime["status"] == "NOT_PROVEN"


def test_v72_offline_certifications_are_independent_evidence():
    result = ProductionExecutionCertification().run()
    assert next(g for g in result["gates"] if g["name"] == "offline_external_reconciliation")["status"] == "PASS"
    assert next(g for g in result["gates"] if g["name"] == "offline_workforce_chaos")["status"] == "PASS"
