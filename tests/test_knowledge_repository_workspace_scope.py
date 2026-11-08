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


def test_supabase_query_filters_by_both_brand_and_workspace():
    class RecordingQuery:
        def __init__(self):
            self.filters = []

        def select(self, *_args, **_kwargs):
            return self

        def eq(self, key, value):
            self.filters.append((key, value))
            return self

        def execute(self):
            return type("Result", (), {"data": []})()

    class RecordingSupabase:
        def __init__(self):
            self.query = RecordingQuery()

        def table(self, _name):
            return self.query

    supabase = RecordingSupabase()
    repo = KnowledgeRepository(supabase_client=supabase)

    assert repo.find_by_brand("brand-shared-id", "workspace-a") == []
    assert supabase.query.filters == [
        ("brand_id", "brand-shared-id"),
        ("workspace_id", "workspace-a"),
    ]


def test_topic_selector_uses_brand_workspace_for_kb_topics():
    from types import SimpleNamespace

    from domain.content.topic_selector import _build_categories_from_knowledge_base

    entry = KnowledgeEntry(
        id="kb-topic-a",
        workspace_id="workspace-a",
        brand_id="brand-shared-id",
        content="B-tree indexes support ordered lookups.",
        metadata={"theme": "databases", "title": "B-tree indexes"},
    )

    class ScopedRepository:
        def __init__(self):
            self.args = None

        def find_by_brand(self, brand_id, workspace_id):
            self.args = (brand_id, workspace_id)
            return [entry]

    repo = ScopedRepository()
    brand = SimpleNamespace(
        id="brand-shared-id",
        name="Brand A",
        workspace_id="workspace-a",
    )

    categories = _build_categories_from_knowledge_base(brand, repo)

    assert repo.args == ("brand-shared-id", "workspace-a")
    assert categories == {"Databases": ["B-tree indexes"]}


def test_topic_selector_refuses_kb_lookup_without_workspace():
    from types import SimpleNamespace

    from domain.content.topic_selector import _build_categories_from_knowledge_base

    class UnscopedRepository:
        called = False

        def find_by_brand(self, *_args):
            self.called = True
            return []

    repo = UnscopedRepository()
    brand = SimpleNamespace(id="brand-shared-id", name="Brand A", workspace_id=None)

    assert _build_categories_from_knowledge_base(brand, repo) is None
    assert repo.called is False
