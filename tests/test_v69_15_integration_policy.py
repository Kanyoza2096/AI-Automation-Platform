from services.business_integration_platform import AutonomyMode, ConnectionState, ConnectorContract, IntegrationConnection
from services.v69_connector_activation_registry import ConnectorRegistryRecord, RegistryState, CertificationEvidence
from services.v69_integration_policy import ApprovalLevel, IntegrationPolicy, IntegrationPolicyPlane, PolicyBlock


def fixture(action="publish", mode=AutonomyMode.EXECUTE):
    c = ConnectorContract("facebook", "meta", "1.0", {"publish"}, set(), {action}, set(), {action: "medium"}, True, True, True)
    conn = IntegrationConnection("w1", "facebook", ConnectionState.EXECUTION_ENABLED, {"publish"}, mode, "c1")
    ev = CertificationEvidence("1", "facebook", "1.0", "fp", "PASS", True, __import__('datetime').datetime.now(__import__('datetime').timezone.utc), None, "ref")
    reg = ConnectorRegistryRecord("w1", "c1", "facebook", "1.0", RegistryState.ACTIVE, ev, 1, "ok")
    return c, conn, reg


def test_publish_requires_council():
    c, conn, reg = fixture()
    d = IntegrationPolicyPlane("w1", conn, c, reg).evaluate("publish")
    assert d.allowed_to_proceed and d.approval.level == ApprovalLevel.COUNCIL


def test_human_policy_can_only_tighten():
    c, conn, reg = fixture()
    p = IntegrationPolicy("w1", human_actions=frozenset({"publish"}))
    d = IntegrationPolicyPlane("w1", conn, c, reg, p).evaluate("publish")
    assert d.approval.level == ApprovalLevel.HUMAN


def test_explicit_deny_blocks():
    c, conn, reg = fixture()
    p = IntegrationPolicy("w1", denied_actions=frozenset({"publish"}))
    d = IntegrationPolicyPlane("w1", conn, c, reg, p).evaluate("publish")
    assert not d.allowed_to_proceed
    assert PolicyBlock.POLICY_DENIED.value in d.reasons


def test_critical_never_downgraded():
    c, conn, reg = fixture("rotate_credentials")
    c = ConnectorContract("facebook", "meta", "1.0", {"publish"}, set(), {"rotate_credentials"}, set(), {"rotate_credentials": "critical"}, True, True, True)
    conn = IntegrationConnection("w1", "facebook", ConnectionState.EXECUTION_ENABLED, {"rotate_credentials"}, AutonomyMode.EXECUTE, "c1")
    d = IntegrationPolicyPlane("w1", conn, c, reg).evaluate("rotate_credentials")
    assert d.approval.level == ApprovalLevel.HUMAN


def test_revoked_or_nonexecution_connection_blocks():
    c, conn, reg = fixture()
    conn = IntegrationConnection("w1", "facebook", ConnectionState.SUGGEST_ENABLED, {"publish"}, AutonomyMode.SUGGEST, "c1")
    d = IntegrationPolicyPlane("w1", conn, c, reg).evaluate("publish")
    assert not d.allowed_to_proceed
    assert PolicyBlock.CONNECTION_NOT_READY.value in d.reasons


def test_missing_capability_blocks():
    c, conn, reg = fixture()
    conn = IntegrationConnection("w1", "facebook", ConnectionState.EXECUTION_ENABLED, set(), AutonomyMode.EXECUTE, "c1")
    d = IntegrationPolicyPlane("w1", conn, c, reg).evaluate("publish")
    assert not d.allowed_to_proceed
    assert PolicyBlock.CAPABILITY_NOT_GRANTED.value in d.reasons


def test_wrong_tenant_rejected_at_construction():
    c, conn, reg = fixture()
    try:
        IntegrationPolicyPlane("w2", conn, c, reg)
    except ValueError as e:
        assert str(e) == PolicyBlock.TENANT_MISMATCH.value
    else:
        raise AssertionError("expected tenant mismatch")


def test_policy_is_tightening_only():
    c, conn, reg = fixture()
    p = IntegrationPolicy("w1", council_actions=frozenset({"publish"}))
    d = IntegrationPolicyPlane("w1", conn, c, reg, p).evaluate("publish")
    assert d.approval.level == ApprovalLevel.COUNCIL


def test_unknown_action_defaults_conservatively():
    c, conn, reg = fixture("custom_action")
    c = ConnectorContract("facebook", "meta", "1.0", {"custom_action"}, set(), {"custom_action"}, set(), {"custom_action": "medium"}, True, True, True)
    conn = IntegrationConnection("w1", "facebook", ConnectionState.EXECUTION_ENABLED, {"custom_action"}, AutonomyMode.EXECUTE, "c1")
    d = IntegrationPolicyPlane("w1", conn, c, reg).evaluate("custom_action")
    assert d.approval.level == ApprovalLevel.COUNCIL
