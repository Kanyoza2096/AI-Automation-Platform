"""Regression tests for legacy embedding_json values returned by storage."""
import json
import pytest
from models.knowledge_entry import KnowledgeEntry

def make_entry(embedding_json):
    return KnowledgeEntry(id="kb-1", workspace_id="ws-1", brand_id="brand-1",
                          content="A grounded source fact.", embedding_json=embedding_json)

@pytest.mark.parametrize(("value", "expected"), [
    ("[]", "[]"), ([], "[]"), ([0.1, 0.2], "[0.1,0.2]"),
    ({"terms": [1, 2]}, '{"terms":[1,2]}'), (None, None),
])
def test_embedding_json_normalizes_legacy_values(value, expected):
    assert make_entry(value).embedding_json == expected

def test_normalized_embedding_json_remains_valid_json():
    assert json.loads(make_entry([0.1, 0.2]).embedding_json) == [0.1, 0.2]

def test_embedding_json_rejects_unsupported_objects():
    with pytest.raises(ValueError, match="embedding_json"):
        make_entry(object())
