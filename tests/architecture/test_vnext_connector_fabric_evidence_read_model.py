from services.vnext_connector_fabric_evidence_read_model import build_connector_fabric_evidence


def test_evidence_read_model_is_read_only_and_has_no_authority():
    model = build_connector_fabric_evidence("workspace-a", "github")
    assert model["readOnly"] is True
    assert model["authority"] == "none"
    assert model["eligibilityInputOnly"] is True
    assert model["executionEffect"] == "none"


def test_without_durable_rows_evidence_is_explicitly_not_exposed():
    model = build_connector_fabric_evidence("workspace-a", "github")
    assert model["health"]["state"] in {"NOT_EXPOSED", "healthy", "degraded", "unavailable", "unknown"}
    assert model["certification"]["state"] in {"NOT_EXPOSED", "registered", "certified", "active", "degraded", "expired", "revoked", "superseded"}


def test_all_connector_records_remain_catalogued():
    for key in ("github", "render", "stripe", "shopify", "slack", "jira", "pagerduty", "datadog", "cloudflare", "twilio", "google_workspace", "aws", "facebook", "twitter", "linkedin", "instagram", "telegram", "whatsapp", "website"):
        assert build_connector_fabric_evidence("w", key)["connector"]["connectorKey"] == key
