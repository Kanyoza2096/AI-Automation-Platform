from services.v67_external_side_effect_sandbox import SandboxProvider
from services.v67_provider_certification_contract import certify_adapter


def test_sandbox_provider_meets_v6711_certification_contract():
    result = certify_adapter(SandboxProvider())
    assert result.status == "passed"
    assert result.checks["provider_idempotency_documented"] is True
    assert result.checks["sandbox_isolation"] is True
    assert result.checks["controlled_termination"] is True


def test_certification_fails_closed_for_non_sandbox_adapter():
    class Unsafe:
        provider_name = "unsafe"
        sandbox = False

        def submit(self, **kwargs):
            return {}

        def reconcile(self, **kwargs):
            return {}

        def isolation_evidence(self):
            return {"isolated": True, "production_access": True}

        def idempotency_evidence(self):
            return {"atomic": False, "key_scoped": False}

        def termination_evidence(self):
            return {"worker_termination_supported": False, "side_effect_after_kill_testable": False}

    result = certify_adapter(Unsafe())
    assert result.status == "blocked"
    assert result.errors


def test_contract_requires_reconciliation_evidence():
    provider = SandboxProvider()
    assert provider.reconcile(idempotency_key="missing", correlation_id="c1")["status"] == "not_found"
