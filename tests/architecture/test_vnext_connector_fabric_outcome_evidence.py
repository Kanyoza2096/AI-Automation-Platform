from services.vnext_connector_fabric_read_model import (
    NOT_EXPOSED,
    connector_fabric_catalog,
    connector_outcome_evidence_coverage,
)


def test_every_connector_has_canonical_receipt_protocol():
    rows = connector_outcome_evidence_coverage()["connectors"]
    assert len(rows) == 19
    assert all(row["receiptProtocol"] == "ProviderReceipt" for row in rows)


def test_only_code_backed_reconciliation_is_reported():
    rows = {r["connectorKey"]: r for r in connector_outcome_evidence_coverage()["connectors"]}
    assert rows["github"]["reconciliation"]["status"] == "IMPLEMENTED"
    assert rows["render"]["reconciliation"]["status"] == "IMPLEMENTED"
    assert all(
        rows[k]["reconciliation"]["status"] == NOT_EXPOSED
        for k in rows if k not in {"github", "render"}
    )


def test_verification_does_not_claim_reconciliation():
    rows = {r["connectorKey"]: r for r in connector_outcome_evidence_coverage()["connectors"]}
    assert rows["stripe"]["verification"]["status"] == "IMPLEMENTED"
    assert rows["stripe"]["reconciliation"]["status"] == NOT_EXPOSED
    assert rows["aws"]["verification"]["status"] == "IMPLEMENTED"


def test_catalog_remains_nineteen_non_ai_connectors():
    keys = {r.connector_key for r in connector_fabric_catalog()}
    assert len(keys) == 19
    assert "gemini" not in keys
    assert "cloudflare_ai" not in keys
