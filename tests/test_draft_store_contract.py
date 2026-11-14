import unittest

from api.v1_stores import DraftStore


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.fail_set = False
        self.fail_delete = False

    async def set(self, key, value, ex=None):
        if self.fail_set:
            raise ConnectionError("redis unavailable")
        self.values[key] = value

    async def get(self, key):
        return self.values.get(key)

    async def keys(self, pattern):
        prefix = pattern.removesuffix("*")
        return [key for key in self.values if key.startswith(prefix)]

    async def delete(self, key):
        if self.fail_delete:
            raise ConnectionError("redis unavailable")
        return int(self.values.pop(key, None) is not None)


class DraftStoreContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_set_persists_assigned_workspace_draft_and_delete_removes_it(self):
        redis = FakeRedis()
        store = DraftStore(redis)

        saved = await store.set(
            "draft-123",
            {"draft_id": "draft-123", "workspace_id": "workspace-a", "state": "draft"},
        )

        self.assertEqual(saved["id"], "draft-123")
        self.assertEqual((await store.get_all())[0]["workspace_id"], "workspace-a")
        self.assertTrue(await store.delete("draft-123"))
        self.assertFalse(await store.delete("draft-123"))
        self.assertEqual(await store.get_all(), [])

    async def test_set_fails_closed_when_configured_redis_is_unavailable(self):
        redis = FakeRedis()
        redis.fail_set = True
        store = DraftStore(redis)

        with self.assertRaisesRegex(RuntimeError, "persistence is unavailable"):
            await store.set("draft-123", {"workspace_id": "workspace-a"})
        self.assertEqual(store._fallback, {})

    async def test_delete_surfaces_redis_failure_instead_of_reporting_not_found(self):
        redis = FakeRedis()
        store = DraftStore(redis)
        await store.set("draft-123", {"workspace_id": "workspace-a"})
        redis.fail_delete = True

        with self.assertRaisesRegex(RuntimeError, "deletion is unavailable"):
            await store.delete("draft-123")


if __name__ == "__main__":
    unittest.main()
