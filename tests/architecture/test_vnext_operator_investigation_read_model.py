from services.vnext_operator_investigation_read_model import build_operator_investigation_read_model


def _execution(**extra):
    base = {
        "workspace_id": "ws-1", "execution_id": "ex-1", "status": "uncertain",
        "provider": "example", "provider_object_id": None, "idempotency_key": "idem-1",
    }
    base.update(extra)
    return base


def test_investigation_is_read_only_and_never_grants_causality():
    result = build_operator_investigation_read_model(
        workspace_id="ws-1", execution=_execution(),
        intent={"workspace_id": "ws-1", "intent_id": "in-1", "decision_id": "d-1", "correlation_id": "corr-1"},
    )
    assert result["authority"]["readOnly"] is True
    assert result["authority"]["actionAuthorityGranted"] is False
    assert result["authority"]["reconciliationAuthorityGranted"] is False
    assert result["authority"]["causalInferenceGranted"] is False
    assert result["causality"]["claim"] is False
    assert result["causality"]["status"] == "ASSOCIATION_ONLY"
    assert result["chain"]["reconciliation"]["state"] == "REQUIRED"


def test_investigation_links_only_explicit_identifiers():
    result = build_operator_investigation_read_model(
        workspace_id="ws-1", execution=_execution(status="succeeded", provider_object_id="obj-1"),
        intent={"workspace_id": "ws-1", "correlation_id": "corr-1"},
        events=[
            {"id": "e1", "correlation_id": "corr-1"},
            {"id": "e2", "message": "same timestamp but unrelated"},
        ],
    )
    assert [e["id"] for e in result["chain"]["events"]["items"]] == ["e1"]
    assert result["chain"]["events"]["associationOnly"] is True


def test_workspace_mismatch_is_rejected():
    try:
        build_operator_investigation_read_model(workspace_id="ws-1", execution=_execution(workspace_id="ws-2"))
    except ValueError as exc:
        assert "workspace mismatch" in str(exc)
    else:
        raise AssertionError("workspace mismatch must be rejected")
