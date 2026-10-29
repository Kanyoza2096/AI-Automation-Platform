import hashlib
import pytest
from services.kdo_engineering_artifacts import (
    EngineeringArtifactConflict, EngineeringArtifactError, EngineeringArtifactNotFound,
    SQLiteEngineeringArtifactRepository,
)


def repo():
    return SQLiteEngineeringArtifactRepository()


def test_put_get_patch_round_trip_and_digest():
    r = repo(); content = b"diff --git a/a.py b/a.py\n+print('hello')\n"
    item = r.put(tenant_id="t1", workspace_id="w1", task_id="task1", kind="patch",
                 filename="change.patch", relative_path="src/a.py", content=content)
    manifest, recovered = r.get(item.artifact_id, tenant_id="t1", workspace_id="w1")
    assert recovered == content
    assert manifest.sha256 == hashlib.sha256(content).hexdigest()
    assert manifest.size_bytes == len(content)


def test_scope_isolation_hides_artifact_existence():
    r = repo(); item = r.put(tenant_id="t1", workspace_id="w1", task_id="x", kind="test_log", filename="test.log", content=b"ok")
    with pytest.raises(EngineeringArtifactNotFound):
        r.get(item.artifact_id, tenant_id="t2", workspace_id="w1")
    with pytest.raises(EngineeringArtifactNotFound):
        r.get(item.artifact_id, tenant_id="t1", workspace_id="w2")


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "a/../../secret", "..\\secret", "./foo"])
def test_unsafe_relative_paths_rejected(path):
    with pytest.raises(EngineeringArtifactError):
        repo().put(tenant_id="t", workspace_id="w", task_id="x", kind="patch", filename="x.patch", relative_path=path, content=b"diff")


def test_snapshot_requires_base_revision():
    with pytest.raises(EngineeringArtifactError, match="snapshot_base_revision_required"):
        repo().put(tenant_id="t", workspace_id="w", task_id="x", kind="repository_snapshot", filename="snapshot.json", content=b"{}")


def test_size_limit_and_empty_patch_rejected():
    r = SQLiteEngineeringArtifactRepository(max_artifact_bytes=3)
    with pytest.raises(EngineeringArtifactError, match="artifact_size_limit_exceeded"):
        r.put(tenant_id="t", workspace_id="w", task_id="x", kind="test_log", filename="x.log", content=b"long")
    with pytest.raises(EngineeringArtifactError, match="empty_patch_rejected"):
        r.put(tenant_id="t", workspace_id="w", task_id="x", kind="patch", filename="x.patch", content=b"")


def test_list_is_scoped_and_validates_limit():
    r = repo()
    r.put(tenant_id="t1", workspace_id="w1", task_id="x", kind="test_log", filename="a.log", content=b"a")
    r.put(tenant_id="t2", workspace_id="w1", task_id="x", kind="test_log", filename="b.log", content=b"b")
    assert len(r.list_task("x", tenant_id="t1", workspace_id="w1")) == 1
    with pytest.raises(EngineeringArtifactError):
        r.list_task("x", tenant_id="t1", workspace_id="w1", limit=0)


def test_artifact_id_collision_does_not_overwrite_existing_blob():
    r = repo()
    r.put(tenant_id="t", workspace_id="w", task_id="x", kind="test_log", filename="a.log", content=b"a", artifact_id="fixed")
    with pytest.raises(EngineeringArtifactConflict):
        r.put(tenant_id="t", workspace_id="w", task_id="x", kind="test_log", filename="b.log", content=b"b", artifact_id="fixed")
    _, content = r.get("fixed", tenant_id="t", workspace_id="w")
    assert content == b"a"
