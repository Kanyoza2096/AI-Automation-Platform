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
