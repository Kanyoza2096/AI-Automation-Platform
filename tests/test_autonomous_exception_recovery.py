from services.autonomous_exception_recovery import (
    AutonomousExceptionRecovery, ExceptionClass, RecoveryAction, RecoveryDisposition,
)


def obs(**kw):
    base = dict(workspace_id="ws", execution_id="exe-1", action_type="crm_schedule_followup",
                observed_status="failed", error="", attempt=0)
    base.update(kw)
    return AutonomousExceptionRecovery().observe(**base)


def test_transient_failure_gets_bounded_auto_retry():
    o = obs(error="timeout from provider", error_code="timeout", attempt=0)
    p = AutonomousExceptionRecovery().evaluate(o, idempotent=True)
    assert o.classification == ExceptionClass.TRANSIENT_PROVIDER
    assert p.disposition == RecoveryDisposition.AUTO_RECOVER
    assert p.action == RecoveryAction.RETRY_EXISTING_INTENT
    assert p.max_attempts == 3
    assert p.authority == "none"


def test_uncertain_state_never_blind_retries():
    o = obs(observed_status="uncertain", error="transport failed after request")
    p = AutonomousExceptionRecovery().evaluate(o, idempotent=True, has_reconciler=True)
    assert o.classification == ExceptionClass.UNCERTAIN_EXTERNAL
    assert p.disposition == RecoveryDisposition.RECONCILE_FIRST
    assert p.action == RecoveryAction.RECONCILE_EXTERNAL_STATE
    assert p.requires_reconciliation is True


def test_uncertain_without_reconciler_requires_review():
    o = obs(observed_status="reconciliation_required")
    p = AutonomousExceptionRecovery().evaluate(o, idempotent=True, has_reconciler=False)
    assert p.disposition == RecoveryDisposition.REVIEW_REQUIRED
    assert p.requires_human_approval is True


def test_partial_execution_is_not_normal_retry():
    o = obs(error="partial delivery", partial_execution=True)
    p = AutonomousExceptionRecovery().evaluate(o, idempotent=True, has_reconciler=True)
    assert o.classification == ExceptionClass.PARTIAL_EXECUTION
    assert p.action == RecoveryAction.RECONCILE_EXTERNAL_STATE


def test_irreversible_action_can_never_auto_recover():
    o = obs(error="timeout", error_code="timeout")
    p = AutonomousExceptionRecovery().evaluate(o, idempotent=True, irreversible=True)
    assert p.disposition == RecoveryDisposition.REVIEW_REQUIRED
    assert p.requires_human_approval is True


def test_policy_and_validation_fail_closed():
    for error, code in [("policy rejected", "policy"), ("invalid parameter", "validation")]:
        o = obs(error=error, error_code=code)
        p = AutonomousExceptionRecovery().evaluate(o, idempotent=True)
        assert p.disposition == RecoveryDisposition.BLOCKED
        assert p.action == RecoveryAction.NONE


def test_authentication_failure_is_not_retried_blindly():
    o = obs(error="authentication failed", error_code="401")
    p = AutonomousExceptionRecovery().evaluate(o, idempotent=True)
    assert o.classification == ExceptionClass.AUTHENTICATION
    assert p.disposition == RecoveryDisposition.REVIEW_REQUIRED
    assert p.requires_human_approval is True


def test_retry_budget_quarantines_repeated_transient_failure():
    o = obs(error="service unavailable", error_code="503", attempt=3)
    p = AutonomousExceptionRecovery().evaluate(o, idempotent=True, prior_failures=3)
    assert p.disposition == RecoveryDisposition.QUARANTINED
    assert p.action == RecoveryAction.NONE


def test_non_idempotent_transient_failure_requires_review():
    o = obs(error="connection reset", error_code="connection")
    p = AutonomousExceptionRecovery().evaluate(o, idempotent=False)
    assert p.disposition == RecoveryDisposition.REVIEW_REQUIRED
    assert p.requires_human_approval is True


def test_auto_recovery_release_is_only_a_kernel_handoff():
    from services.autonomous_exception_recovery import build_recovery_release
    o = obs(error="timeout", error_code="timeout")
    p = AutonomousExceptionRecovery().evaluate(o, idempotent=True)
    r = build_recovery_release(p, intent_id="act_123")
    assert r.intent_id == "act_123"
    assert r.approved is True
    assert r.to_dict()["execution_authority"] == "action_kernel"


def test_non_auto_plan_cannot_be_released():
    from services.autonomous_exception_recovery import build_recovery_release
    o = obs(observed_status="uncertain")
    p = AutonomousExceptionRecovery().evaluate(o, idempotent=True, has_reconciler=True)
    try:
        build_recovery_release(p, intent_id="act_123")
        assert False
    except ValueError:
        pass

class _FakeResult:
    def __init__(self, data):
        self.data = data

class _FakeTable:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.payload = None
    def upsert(self, payload, **kwargs):
        self.payload = payload
        self.db.writes.append((self.name, "upsert", payload))
        return self
    def update(self, payload):
        self.payload = payload
        self.db.writes.append((self.name, "update", payload))
        return self
    def eq(self, *args):
        return self
    def execute(self):
        return _FakeResult(True)

class _FakeDB:
    def __init__(self, rpc_data=True):
        self.rpc_data = rpc_data
        self.writes = []
    def table(self, name):
        return _FakeTable(self, name)
    def rpc(self, name, payload):
        self.writes.append((name, "rpc", payload))
        class _Rpc: 
            def __init__(self, data): self.data = data
            def execute(self): return self
        return _Rpc(self.rpc_data)


def test_coordinator_releases_only_terminal_failed_intent():
    import asyncio
    from services.autonomous_exception_recovery import RecoveryCoordinator
    db = _FakeDB(True)
    row = {
        "workspace_id": "ws", "intent_id": "act_123", "action_type": "crm_schedule_followup",
        "attempts": 1, "correlation_id": "corr-1", "status": "failed",
    }
    result = {"status": "failed", "execution_id": "exe-1", "error": "timeout", "output": {}}
    out = asyncio.run(RecoveryCoordinator(db).handle_failure(row=row, result=result, idempotent=True))
    assert out["status"] == "released"
    assert any(w[0] == "release_action_outbox_for_recovery" for w in db.writes)
    assert any(w[0] == "autonomous_recovery_plans" for w in db.writes)


def test_coordinator_uncertain_state_stays_out_of_retry_release():
    import asyncio
    from services.autonomous_exception_recovery import RecoveryCoordinator
    db = _FakeDB(True)
    row = {
        "workspace_id": "ws", "intent_id": "act_123", "action_type": "crm_schedule_followup",
        "attempts": 1, "status": "reconciliation_required",
    }
    result = {"status": "uncertain", "execution_id": "exe-1", "error": "transport failed after request",
              "reconciliation_required": True}
    out = asyncio.run(RecoveryCoordinator(db).handle_failure(row=row, result=result, idempotent=True, has_reconciler=False))
    assert out["status"] == "review_required"
    assert not any(w[0] == "release_action_outbox_for_recovery" for w in db.writes)
