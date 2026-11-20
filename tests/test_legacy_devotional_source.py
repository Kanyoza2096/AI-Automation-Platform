import hashlib
import json

import pytest

from services import legacy_devotional_source as source


def test_git_blob_digest_matches_git_object_format():
    payload = b"fixture"
    expected = hashlib.sha1(b"blob 7" + bytes([0]) + payload).hexdigest()
    assert source._git_blob_sha(payload) == expected


def test_pinned_blob_decoder_rejects_wrong_object_identity():
    with pytest.raises(source.PinnedDatasetError, match="identity_mismatch"):
        source.decode_pinned_blob_response({
            "sha": "0" * 40,
            "encoding": "base64",
            "content": "Zml4dHVyZQ==",
        })



def test_pinned_blob_decoder_rejects_malformed_base64_even_with_expected_sha():
    with pytest.raises(source.PinnedDatasetError, match="invalid_blob_base64"):
        source.decode_pinned_blob_response({
            "sha": source.BLOB_SHA,
            "encoding": "base64",
            "content": "%%%not-base64%%%",
        })

def test_record_snapshot_is_deterministic_and_bound_to_pinned_record():
    rows = [{
        "entry_id": "01-01-morning",
        "content_blocks": ["A devotional quotation.", "The devotional text."],
        "primary_reference": {"raw": "Joshua 5:12", "osis": ["Josh.5.12"]},
    }]
    first = source.extract_record_snapshot(rows, "01-01-morning")
    second = source.extract_record_snapshot(rows, "01-01-morning")

    assert first == second
    assert first["source_id"].endswith(":record:01-01-morning")
    assert first["dataset_blob_sha"] == source.BLOB_SHA
    assert json.loads(first["snapshot_text"])["entry_id"] == "01-01-morning"
    assert len(first["snapshot_sha256"]) == 64


@pytest.mark.parametrize("record_id", ["", "missing"])
def test_record_snapshot_rejects_missing_or_unknown_record_id(record_id):
    with pytest.raises(source.PinnedDatasetError):
        source.extract_record_snapshot([], record_id)


def test_record_snapshot_rejects_ambiguous_record_id():
    row = {
        "entry_id": "01-01-morning",
        "content_blocks": ["text"],
        "primary_reference": None,
    }
    with pytest.raises(source.PinnedDatasetError, match="ambiguous"):
        source.extract_record_snapshot([row, row], "01-01-morning")



def test_record_metadata_uses_existing_hmac_contract_and_requires_explicit_trust_class():
    rows = [{
        "entry_id": "01-01-morning",
        "content_blocks": ["A devotional quotation.", "The devotional text."],
        "primary_reference": {"raw": "Joshua 5:12", "osis": ["Josh.5.12"]},
    }]
    metadata = source.build_attested_record_metadata(
        rows=rows,
        record_id="01-01-morning",
        workspace_id="workspace-1",
        secret="test-secret",
        trust_class="publisher_primary",
    )
    from services.autonomous_knowledge_evidence_reviewer import (
        verify_source_snapshot_attestation,
    )

    assert metadata["source_snapshot_origin"] == "trusted_server_ingestion"
    assert verify_source_snapshot_attestation(metadata, "workspace-1", "test-secret")
    with pytest.raises(source.PinnedDatasetError, match="trust_class"):
        source.build_attested_record_metadata(
            rows=rows,
            record_id="01-01-morning",
            workspace_id="workspace-1",
            secret="test-secret",
            trust_class="unreviewed",
        )

def test_pinned_fetch_rejects_invalid_content_length(monkeypatch):
    class FakeResponse:
        status_code = 200
        headers = {"Content-Length": "not-a-number"}

        def close(self):
            self.closed = True

        def iter_content(self, chunk_size):
            return iter(())

    response = FakeResponse()
    import requests
    from services.web_intelligence import base as web_base

    monkeypatch.setattr(web_base, "validate_external_url", lambda _url: (True, "ok"))
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: response)
    with pytest.raises(source.PinnedDatasetError, match="content_length_invalid"):
        source.fetch_pinned_dataset()
    assert response.closed
\n