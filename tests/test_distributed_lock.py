from core.distributed_lock import DistributedLock, LockMode
from core.redis_client import RedisConfig, UpstashRedisClient


def test_memory_fallback_set_nx_and_expiry_are_atomic():
    client = UpstashRedisClient(RedisConfig(enabled=False))
    assert client.set("k", "a", ex=60, nx=True)
    assert not client.set("k", "b", ex=60, nx=True)
    assert client.get("k") == "a"


def test_distributed_lock_uses_nx_and_token_fencing_with_fallback_disabled():
    client = UpstashRedisClient(RedisConfig(enabled=False))
    lock = DistributedLock("x", client, allow_local_fallback=False)
    assert not lock.acquire(require_distributed=True)
    assert lock.mode == LockMode.UNAVAILABLE


def test_distributed_lock_local_mode_is_explicit():
    lock = DistributedLock("x", None, allow_local_fallback=True)
    assert lock.acquire()
    assert lock.mode == LockMode.DEGRADED_LOCAL
    lock.release()


def test_compare_and_delete_never_deletes_new_owner_in_memory():
    client = UpstashRedisClient(RedisConfig(enabled=False))
    client.set("lock:x", "owner-b", ex=60)
    assert not client.compare_and_delete("lock:x", "owner-a")
    assert client.get("lock:x") == "owner-b"
    assert client.compare_and_delete("lock:x", "owner-b")
    assert client.get("lock:x") is None

def test_distributed_set_refuses_in_memory_fallback_when_redis_disabled():
    import pytest
    from core.redis_client import RedisConnectionError

    client = UpstashRedisClient(RedisConfig(enabled=False))
    with pytest.raises(RedisConnectionError, match="Redis unavailable"):
        client.set_distributed("lock:x", "owner", ex=60, nx=True)
    assert client.get("lock:x") is None


def test_distributed_lock_fails_closed_when_real_redis_write_fails():
    class FailingRedis:
        is_available = True

        def set_distributed(self, *args, **kwargs):
            raise RuntimeError("simulated Redis write failure")

        def set(self, *args, **kwargs):
            # A normal client fallback must not be used for distributed locks.
            return True

    lock = DistributedLock("x", FailingRedis(), allow_local_fallback=True)
    assert not lock.acquire(require_distributed=True)
    assert lock.mode == LockMode.UNAVAILABLE



def test_distributed_lock_reports_contention_separately_from_unavailable():
    class ContendedRedis:
        is_available = True

        def set_distributed(self, *args, **kwargs):
            return False

        def inspect_distributed_lock(self, key):
            assert key == "lock:brand:test:publish"
            return {"exists": True, "ttl_seconds": 123}

    lock = DistributedLock(
        "brand:test:publish",
        ContendedRedis(),
        allow_local_fallback=False,
    )
    assert not lock.acquire(require_distributed=True)
    assert lock.mode == LockMode.CONTENDED


def test_distributed_lock_keeps_unavailable_for_redis_write_errors():
    class FailingRedis:
        is_available = True

        def set_distributed(self, *args, **kwargs):
            raise RuntimeError("simulated transport failure")

    lock = DistributedLock("brand:test:publish", FailingRedis(), allow_local_fallback=False)
    assert not lock.acquire(require_distributed=True)
    assert lock.mode == LockMode.UNAVAILABLE


def test_lock_inspection_is_strict_and_does_not_use_memory_fallback():
    import pytest
    from core.redis_client import RedisConnectionError

    client = UpstashRedisClient(RedisConfig(enabled=False))
    with pytest.raises(RedisConnectionError, match="Redis unavailable"):
        client.inspect_distributed_lock("lock:x")


def test_lock_inspection_reads_upstash_existence_and_ttl_without_owner_token(monkeypatch):
    client = UpstashRedisClient(RedisConfig(enabled=False))
    client.config.enabled = True
    client.mode = "upstash"
    client._available = True
    client._request_budget = 100
    calls = []

    def fake_cmd(*args):
        calls.append(args)
        return {"result": [1, 87]}

    monkeypatch.setattr(client, "_cmd", fake_cmd)
    result = client.inspect_distributed_lock("lock:brand:test:publish")

    assert result == {"exists": True, "ttl_seconds": 87}
    assert calls == [("EVAL", client.inspect_distributed_lock.__code__.co_consts[1], "1", "lock:brand:test:publish")]
    assert client._requests_used == 1


def test_lock_inspection_reports_missing_key_with_redis_ttl_sentinel(monkeypatch):
    client = UpstashRedisClient(RedisConfig(enabled=False))
    client.config.enabled = True
    client.mode = "upstash"
    client._available = True
    client._request_budget = 100
    monkeypatch.setattr(client, "_cmd", lambda *args: {"result": [0, -2]})

    assert client.inspect_distributed_lock("lock:missing") == {
        "exists": False,
        "ttl_seconds": -2,
    }
