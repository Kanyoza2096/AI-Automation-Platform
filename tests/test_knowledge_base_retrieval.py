import unittest

from models.knowledge_entry import KnowledgeEntry
from services.knowledge_base import KnowledgeBase


class FakeKnowledgeRepository:
    def __init__(self, entries):
        self.entries = list(entries)

    def find_by_brand(self, brand_id, workspace_id):
        return [
            entry for entry in self.entries
            if entry.brand_id == brand_id and entry.workspace_id == workspace_id
        ]


class ProductionKnowledgeBaseRetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.brand_id = "brand-shipsandshrugs-test"
        self.workspace_id = "workspace-test"
        self.entries = [
            KnowledgeEntry(
                id="gold-btree",
                workspace_id=self.workspace_id,
                brand_id=self.brand_id,
                content="B-tree indexes support ordered database lookups and range scans.",
                doc_type="knowledge_chunk",
                metadata={"record_id": "gold-btree"},
            ),
            KnowledgeEntry(
                id="negative-hash",
                workspace_id=self.workspace_id,
                brand_id=self.brand_id,
                content="Hash maps use hashing to locate keys and values.",
                doc_type="knowledge_chunk",
                metadata={"record_id": "negative-hash"},
            ),
            KnowledgeEntry(
                id="foreign-workspace-record",
                workspace_id="another-workspace",
                brand_id=self.brand_id,
                content="Disjoint sets and union-find support Kruskal minimum spanning tree cycle detection.",
                doc_type="knowledge_chunk",
                metadata={"record_id": "foreign-workspace-record"},
            ),
        ]
        self.repo = FakeKnowledgeRepository(self.entries)
        self.kb = KnowledgeBase(
            brand_id=self.brand_id,
            workspace_id=self.workspace_id,
            repository=self.repo,
        )
        await self.kb.initialize()

    async def test_real_knowledge_base_ranks_relevant_record_first(self):
        results = await self.kb.search("B-tree ordered database range scans", 5)
        self.assertTrue(results)
        self.assertEqual(results[0][0].id, "gold-btree")
        self.assertGreater(results[0][1], 0)

    async def test_initialization_indexes_all_nonempty_records(self):
        stats = self.kb.get_stats()
        self.assertEqual(stats["documents"], 2)
        self.assertGreater(stats["unique_terms"], 0)

    async def test_same_brand_id_in_another_workspace_is_not_indexed(self):
        stats = self.kb.get_stats()
        self.assertEqual(stats["documents"], 2)
        results = await self.kb.search("disjoint sets union-find Kruskal", 5)
        self.assertEqual(results, [])

    async def test_unmatched_query_returns_no_results(self):
        results = await self.kb.search("quantum entanglement telescope", 5)
        self.assertEqual(results, [])

    async def test_synthetic_workspace_scope_cannot_build_knowledge_index(self):
        kb = KnowledgeBase(
            brand_id=self.brand_id,
            workspace_id="default",
            repository=self.repo,
        )
        with self.assertRaisesRegex(RuntimeError, "active tenant workspace_id"):
            await kb.initialize()


if __name__ == "__main__":
    unittest.main()
