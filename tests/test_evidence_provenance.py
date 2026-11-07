from core.evidence_provenance import PROVENANCE_SCHEMA, attach_evidence_provenance


def test_provenance_envelope_identifies_backend_source_and_projection_class():
    payload = {"state": "LIVE", "workspaceId": "workspace-1"}
    result = attach_evidence_provenance(
        payload,
        source="kdo.command_center.read_model",
        authority="kdo.backend.command_center",
        data_class="DERIVED",
    )

    provenance = result["evidenceProvenance"]
    assert provenance["schema"] == PROVENANCE_SCHEMA
    assert provenance["source"] == "kdo.command_center.read_model"
    assert provenance["authority"] == "kdo.backend.command_center"
    assert provenance["dataClass"] == "DERIVED"
    assert provenance["observedAt"]
    assert provenance["evidenceFingerprint"].startswith("sha256:")
    assert len(provenance["evidenceFingerprint"]) == 71
    assert "evidenceProvenance" not in payload


def test_fingerprint_is_deterministic_for_same_payload():
    kwargs = {
        "source": "kdo.system.health",
        "authority": "kdo.backend.health_checker",
        "data_class": "DERIVED",
    }
    first = attach_evidence_provenance({"status": "healthy"}, **kwargs)
    second = attach_evidence_provenance({"status": "healthy"}, **kwargs)
    assert first["evidenceProvenance"]["evidenceFingerprint"] == second["evidenceProvenance"]["evidenceFingerprint"]


def test_fingerprint_changes_when_evidence_payload_changes():
    kwargs = {
        "source": "kdo.system.health",
        "authority": "kdo.backend.health_checker",
        "data_class": "DERIVED",
    }
    first = attach_evidence_provenance({"status": "healthy"}, **kwargs)
    second = attach_evidence_provenance({"status": "degraded"}, **kwargs)
    assert first["evidenceProvenance"]["evidenceFingerprint"] != second["evidenceProvenance"]["evidenceFingerprint"]


def test_invalid_data_class_is_rejected():
    try:
        attach_evidence_provenance(
            {"state": "LIVE"},
            source="kdo.test",
            authority="kdo.backend.test",
            data_class="UNKNOWN",
        )
    except ValueError as exc:
        assert "data_class" in str(exc)
    else:
        raise AssertionError("invalid data class must be rejected")
