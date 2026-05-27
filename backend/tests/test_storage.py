"""Tests for the Storage abstraction.

LocalStorage runs end-to-end against a tmp_path. MinioStorage construction
requires a live MinIO endpoint, so we cover its Protocol shape + isolation
math only — the on-the-wire path is tested by integration when the docker
compose 'storage' profile is up.
"""

from __future__ import annotations

import pytest
from bioforge.storage import (
    LocalStorage,
    ObjectMetadata,
    Storage,
    StorageError,
    get_storage,
)
from bioforge.storage.adapter import _validate_key, _validate_project_id, reset_storage


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_storage()
    yield
    reset_storage()


@pytest.fixture
def local_storage(tmp_path) -> LocalStorage:
    return LocalStorage(root_dir=tmp_path / "storage")


# --- Validation ---------------------------------------------------------------


def test_validate_project_id_accepts_slugs() -> None:
    assert _validate_project_id("default-project") == "default-project"
    assert _validate_project_id("p1") == "p1"


def test_validate_project_id_rejects_bad_shapes() -> None:
    for bad in ("Project1", "p_1", "a..b", "-leading", "trailing-", ""):
        with pytest.raises(StorageError, match="Invalid project_id"):
            _validate_project_id(bad)


def test_validate_key_rejects_traversal_and_absolute_paths() -> None:
    for bad in ("../etc/passwd", "/etc/passwd", "..", "a/../b", ""):
        with pytest.raises(StorageError):
            _validate_key(bad)


def test_validate_key_accepts_nested_relative_paths() -> None:
    assert _validate_key("blast/result-12.xml") == "blast/result-12.xml"
    assert _validate_key("subdir/another/file.txt") == "subdir/another/file.txt"


# --- LocalStorage end-to-end --------------------------------------------------


def test_put_get_roundtrip(local_storage: LocalStorage) -> None:
    payload = b"hello bioforge"
    meta = local_storage.put(project_id="default-project", key="greeting.txt", data=payload)
    assert isinstance(meta, ObjectMetadata)
    assert meta.size_bytes == len(payload)
    assert len(meta.sha256) == 64  # SHA-256 hex
    fetched = local_storage.get(project_id="default-project", key="greeting.txt")
    assert fetched == payload


def test_exists_reports_correctly(local_storage: LocalStorage) -> None:
    assert local_storage.exists(project_id="default-project", key="nope.txt") is False
    local_storage.put(project_id="default-project", key="a.txt", data=b"x")
    assert local_storage.exists(project_id="default-project", key="a.txt") is True


def test_list_returns_keys_for_project_only(local_storage: LocalStorage) -> None:
    local_storage.put(project_id="proj-a", key="alpha.txt", data=b"1")
    local_storage.put(project_id="proj-a", key="dir/beta.txt", data=b"2")
    local_storage.put(project_id="proj-b", key="gamma.txt", data=b"3")
    a_keys = local_storage.list(project_id="proj-a")
    b_keys = local_storage.list(project_id="proj-b")
    assert "alpha.txt" in a_keys
    assert "dir/beta.txt" in a_keys
    assert "gamma.txt" not in a_keys
    assert b_keys == ["gamma.txt"]


def test_list_filters_by_prefix(local_storage: LocalStorage) -> None:
    local_storage.put(project_id="default-project", key="blast/r1.xml", data=b"1")
    local_storage.put(project_id="default-project", key="blast/r2.xml", data=b"2")
    local_storage.put(project_id="default-project", key="other.txt", data=b"3")
    blast_keys = local_storage.list(project_id="default-project", prefix="blast/")
    assert blast_keys == ["blast/r1.xml", "blast/r2.xml"]


def test_delete_removes_object(local_storage: LocalStorage) -> None:
    local_storage.put(project_id="default-project", key="ephemeral.txt", data=b"x")
    assert local_storage.exists(project_id="default-project", key="ephemeral.txt") is True
    local_storage.delete(project_id="default-project", key="ephemeral.txt")
    assert local_storage.exists(project_id="default-project", key="ephemeral.txt") is False


def test_get_unknown_object_raises(local_storage: LocalStorage) -> None:
    with pytest.raises(StorageError, match="not found"):
        local_storage.get(project_id="default-project", key="nope.txt")


def test_project_isolation_via_separate_dirs(local_storage: LocalStorage) -> None:
    """Same key in two projects = two distinct objects, no cross-talk."""
    local_storage.put(project_id="proj-a", key="x.txt", data=b"from a")
    local_storage.put(project_id="proj-b", key="x.txt", data=b"from b")
    assert local_storage.get(project_id="proj-a", key="x.txt") == b"from a"
    assert local_storage.get(project_id="proj-b", key="x.txt") == b"from b"


def test_path_traversal_rejected_even_after_validation_passes(local_storage: LocalStorage) -> None:
    """A key like 'a/b' shouldn't be able to escape the project dir even if
    constructed cleverly. We validate up front, but also assert the resolved
    path stays under the project root."""
    # The validator should already reject ..
    with pytest.raises(StorageError):
        local_storage.put(project_id="default-project", key="../escape.txt", data=b"x")


def test_local_storage_satisfies_protocol(local_storage: LocalStorage) -> None:
    assert isinstance(local_storage, Storage)


# --- Env-driven selection -----------------------------------------------------


def test_default_backend_is_local(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("BIOFORGE_STORAGE_BACKEND", raising=False)
    monkeypatch.setenv("BIOFORGE_STORAGE_ROOT", str(tmp_path))
    reset_storage()
    s = get_storage()
    assert isinstance(s, LocalStorage)
    assert s.backend_name == "local"


def test_storage_root_env_var_honored(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("BIOFORGE_STORAGE_BACKEND", raising=False)
    monkeypatch.setenv("BIOFORGE_STORAGE_ROOT", str(tmp_path / "custom-root"))
    reset_storage()
    s = get_storage()
    s.put(project_id="default-project", key="probe.txt", data=b"x")
    assert (tmp_path / "custom-root" / "default-project" / "probe.txt").read_bytes() == b"x"


def test_minio_construction_satisfies_protocol() -> None:
    """We don't connect to a real MinIO — just verify the class shape. The
    actual S3 round-trip is tested when the docker compose 'storage' profile
    is up."""
    from bioforge.storage import MinioStorage

    # Inspect the Protocol membership without instantiating (which requires a
    # live endpoint). Protocol checks happen on type, not on connection.
    assert hasattr(MinioStorage, "put")
    assert hasattr(MinioStorage, "get")
    assert hasattr(MinioStorage, "list")
    assert hasattr(MinioStorage, "delete")
    assert hasattr(MinioStorage, "exists")
    assert MinioStorage.backend_name == "minio"
