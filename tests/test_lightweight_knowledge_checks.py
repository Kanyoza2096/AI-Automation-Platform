import hashlib

from services.lightweight_knowledge_checks import check_knowledge_entry


def test_lightweight_check_records_integrity_without_approving():
    content = "Kruskal's algorithm adds edges in ascending weight order."
    result = check_knowledge_entry(content, {
        "source_url": "https://example.org/kruskal",
        "source_id": "book:chapter-4",
        "trust_class": "academic",
    })
    assert result["status"] == "checked"
    assert result["content_sha256"] == hashlib.sha256(content.encode()).hexdigest()
    assert result["source_url_valid_https"] is True
    assert result["stable_source_id_present"] is True
    assert result["trust_class_recognized"] is True
    assert result["verified"] is False
    assert result["approval_granted"] is False
    assert result["publishing_evidence_authorized"] is False


def test_missing_provenance_is_warning_not_automatic_rejection():
    result = check_knowledge_entry("A useful explanation.", {})
    assert result["status"] == "checked"
    assert "source_url_missing" in result["warnings"]
    assert "stable_source_id_missing" in result["warnings"]
    assert "trust_class_missing" in result["warnings"]
    assert result["verified"] is False


def test_obvious_content_integrity_problems_need_attention():
    result = check_knowledge_entry("TODO: lorem ipsum")
    assert result["status"] == "needs_attention"
    assert "placeholder_content" in result["issues"]


def test_untrusted_or_malformed_source_metadata_never_passes_as_trusted():
    result = check_knowledge_entry("A factual statement.", {
        "source_url": "http://example.org/source",
        "source_id": "unverified-entry:123",
        "trust_class": "random",
    })
    assert result["source_url_valid_https"] is False
    assert result["stable_source_id_present"] is False
    assert result["trust_class_recognized"] is False
    assert result["publishing_evidence_authorized"] is False
