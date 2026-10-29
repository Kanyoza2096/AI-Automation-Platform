import json
from services.enterprise_production_certification import EnterpriseProductionCertificationService


def test_phase10_allows_machine_readable_external_evidence_without_self_certification():
    checks = [
        "architecture_integrity", "security_controls", "business_operating_model",
        "agent_control_plane", "execution_governance", "universal_provenance",
        "business_reliability", "recovery_readiness", "external_runtime_evidence",
    ]
    evidence = [{"check_id": c, "status": "PASS", "source": "release-runner", "evidence_id": c} for c in checks]
    result = EnterpriseProductionCertificationService().assess(workspace_id="w1", evidence=evidence)
    payload = {
        "schema": result.schema,
        "status": result.status.value,
        "production_certified": result.certifies_production,
        "blocking_reasons": result.blocking_reasons,
        "fingerprint": result.fingerprint,
    }
    assert json.loads(json.dumps(payload))["production_certified"] is True
    assert result.release_authority == "external_production_release_authority"
