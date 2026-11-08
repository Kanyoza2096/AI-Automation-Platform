from models.knowledge_entry import KnowledgeEntry
from repositories.knowledge_repository import KnowledgeRepository


def _entry(record_id, workspace_id):
    return KnowledgeEntry(
        id=record_id,
        workspace_id=workspace_id,
        brand_id="brand-shared-id",
        content=f"Knowledge for {workspace_id}",
        doc_type="knowledge_chunk",
        metadata={},
    )


def test_find_by_brand_returns_only_entries_from_requested_workspace():
    repo = KnowledgeRepository()
    repo.save(_entry("kb-a", "workspace-a"))
    repo.save(_entry("kb-b", "workspace-b"))

    results = repo.find_by_brand("brand-shared-id", "workspace-a")

    assert [entry.id for entry in results] == ["kb-a"]


def test_find_by_brand_requires_workspace_scope():
    repo = KnowledgeRepository()
    repo.save(_entry("kb-a", "workspace-a"))

    try:
        repo.find_by_brand("brand-shared-id", "")
    except ValueError as exc:
        assert "workspace_id is required" in str(exc)
    else:
        raise AssertionError("unscoped knowledge lookup should be rejected")
