"""Storage Protocol + Local + MinIO implementations.

Every operation takes (project_id, key). The implementation enforces
project_id isolation by namespacing the key — Local uses a per-project
filesystem subdir; MinIO uses an object-key prefix. There is no API for
crossing the boundary, because there shouldn't be one until auth lands.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

# Project IDs use the same slug regex as the Phase 0 DB layer.
_PROJECT_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# Keys must be relative paths, no `..`, no leading slash.
_KEY_RE = re.compile(r"^[A-Za-z0-9_./-]+$")


class StorageError(RuntimeError):
    """Raised on storage transport / validation failures.

    Distinct from generic exceptions so callers can catch storage problems
    specifically and decide whether to retry, surface as ToolError, etc.
    """


@dataclass
class ObjectMetadata:
    """Information about a stored object — what the agent surfaces to the user."""

    project_id: str
    key: str
    size_bytes: int
    sha256: str
    content_type: str | None = None


def _validate_project_id(project_id: str) -> str:
    if not _PROJECT_ID_RE.match(project_id):
        raise StorageError(
            f"Invalid project_id {project_id!r}: must be lowercase alphanumerics + hyphens "
            "(e.g. 'default-project'). Same shape as the Phase 0 projects.id column."
        )
    return project_id


def _validate_key(key: str) -> str:
    if not key or ".." in key or key.startswith("/"):
        raise StorageError(f"Invalid storage key {key!r}: must be a relative path with no '..'.")
    if not _KEY_RE.match(key):
        raise StorageError(f"Storage key {key!r} contains unsupported characters. Allowed: A-Z a-z 0-9 _ . / -")
    return key


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@runtime_checkable
class Storage(Protocol):
    """The contract every storage implementation satisfies."""

    backend_name: str

    def put(
        self,
        *,
        project_id: str,
        key: str,
        data: bytes,
        content_type: str | None = None,
    ) -> ObjectMetadata: ...

    def get(self, *, project_id: str, key: str) -> bytes: ...

    def exists(self, *, project_id: str, key: str) -> bool: ...

    def list(self, *, project_id: str, prefix: str = "") -> list[str]: ...

    def delete(self, *, project_id: str, key: str) -> None: ...


# --- LocalStorage (default) ---------------------------------------------------


class LocalStorage:
    """Filesystem-backed storage under a root directory.

    Layout:
      <root>/<project_id>/<key>

    Project-id isolation is enforced by validating project_id + key, then
    constructing the absolute path with `pathlib.Path.resolve` and asserting
    the resolved path stays within the project's subdir. This rejects any
    key that tries to break out via symlinks or unicode tricks.
    """

    backend_name = "local"

    def __init__(self, root_dir: str | os.PathLike[str] = "/data/storage") -> None:
        self.root = Path(root_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, project_id: str, key: str) -> Path:
        _validate_project_id(project_id)
        _validate_key(key)
        project_root = (self.root / project_id).resolve()
        # Allow creating the dir on first put().
        project_root.mkdir(parents=True, exist_ok=True)
        target = (project_root / key).resolve()
        # Reject path traversal — the resolved path must stay under project_root.
        try:
            target.relative_to(project_root)
        except ValueError as e:
            raise StorageError(f"Resolved path {target} escaped project root {project_root} — refusing.") from e
        return target

    def put(
        self,
        *,
        project_id: str,
        key: str,
        data: bytes,
        content_type: str | None = None,
    ) -> ObjectMetadata:
        target = self._path_for(project_id, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return ObjectMetadata(
            project_id=project_id,
            key=key,
            size_bytes=len(data),
            sha256=_hash_bytes(data),
            content_type=content_type,
        )

    def get(self, *, project_id: str, key: str) -> bytes:
        target = self._path_for(project_id, key)
        if not target.is_file():
            raise StorageError(f"Object not found: project={project_id!r} key={key!r}")
        return target.read_bytes()

    def exists(self, *, project_id: str, key: str) -> bool:
        try:
            target = self._path_for(project_id, key)
        except StorageError:
            return False
        return target.is_file()

    def list(self, *, project_id: str, prefix: str = "") -> list[str]:
        _validate_project_id(project_id)
        project_root = (self.root / project_id).resolve()
        if not project_root.is_dir():
            return []
        results: list[str] = []
        for path in project_root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(project_root).as_posix()
            if rel.startswith(prefix):
                results.append(rel)
        return sorted(results)

    def delete(self, *, project_id: str, key: str) -> None:
        target = self._path_for(project_id, key)
        if target.is_file():
            target.unlink()


# --- MinioStorage -------------------------------------------------------------


class MinioStorage:
    """MinIO / S3-compatible storage with project_id isolation by key prefix.

    Layout in the configured bucket:
      <project_id>/<key>

    No multi-tenancy boundary inside MinIO yet — that comes with auth in
    Phase 6+. For now the same single-process gatekeeping that protects
    LocalStorage is what protects MinioStorage: we validate project_id +
    key, build the full S3 key, and reject anything that tries to climb out.
    """

    backend_name = "minio"

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        bucket: str | None = None,
        secure: bool = False,
    ) -> None:
        from minio import Minio

        self.bucket = bucket or os.environ.get("BIOFORGE_MINIO_BUCKET", "bioforge")
        self._client = Minio(
            endpoint or os.environ.get("BIOFORGE_MINIO_ENDPOINT", "minio:9000"),
            access_key=access_key or os.environ.get("BIOFORGE_MINIO_ACCESS_KEY", "bioforge"),
            secret_key=secret_key or os.environ.get("BIOFORGE_MINIO_SECRET_KEY", "bioforge-dev"),
            secure=secure,
        )
        # Ensure the bucket exists. MinIO returns 200 + a 'BucketAlreadyExists'-style
        # error if we double-create; the make_bucket_exists check avoids that.
        if not self._client.bucket_exists(self.bucket):
            self._client.make_bucket(self.bucket)

    def _object_name(self, project_id: str, key: str) -> str:
        _validate_project_id(project_id)
        _validate_key(key)
        return f"{project_id}/{key}"

    def put(
        self,
        *,
        project_id: str,
        key: str,
        data: bytes,
        content_type: str | None = None,
    ) -> ObjectMetadata:
        from io import BytesIO

        object_name = self._object_name(project_id, key)
        try:
            self._client.put_object(
                self.bucket,
                object_name,
                BytesIO(data),
                length=len(data),
                content_type=content_type or "application/octet-stream",
            )
        except Exception as e:  # noqa: BLE001
            raise StorageError(f"MinIO put_object failed: {type(e).__name__}: {e}") from e
        return ObjectMetadata(
            project_id=project_id,
            key=key,
            size_bytes=len(data),
            sha256=_hash_bytes(data),
            content_type=content_type,
        )

    def get(self, *, project_id: str, key: str) -> bytes:
        object_name = self._object_name(project_id, key)
        try:
            response = self._client.get_object(self.bucket, object_name)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        except Exception as e:  # noqa: BLE001
            raise StorageError(f"MinIO get_object failed: {type(e).__name__}: {e}") from e

    def exists(self, *, project_id: str, key: str) -> bool:
        try:
            object_name = self._object_name(project_id, key)
        except StorageError:
            return False
        try:
            self._client.stat_object(self.bucket, object_name)
            return True
        except Exception:  # noqa: BLE001
            return False

    def list(self, *, project_id: str, prefix: str = "") -> list[str]:
        _validate_project_id(project_id)
        full_prefix = f"{project_id}/{prefix}"
        results: list[str] = []
        for obj in self._client.list_objects(self.bucket, prefix=full_prefix, recursive=True):
            # Strip the project_id/ prefix to return relative keys.
            name = obj.object_name
            if isinstance(name, str) and name.startswith(f"{project_id}/"):
                results.append(name[len(project_id) + 1 :])
        return sorted(results)

    def delete(self, *, project_id: str, key: str) -> None:
        object_name = self._object_name(project_id, key)
        try:
            self._client.remove_object(self.bucket, object_name)
        except Exception as e:  # noqa: BLE001
            raise StorageError(f"MinIO remove_object failed: {type(e).__name__}: {e}") from e


# --- Selection -----------------------------------------------------------------


_STORAGE_SINGLETON: Storage | None = None


def get_storage() -> Storage:
    """Return the configured Storage. Env-driven selection.

    BIOFORGE_STORAGE_BACKEND values:
      - 'local' (default): LocalStorage at /data/storage or BIOFORGE_STORAGE_ROOT.
      - 'minio': MinioStorage at BIOFORGE_MINIO_ENDPOINT.
    """
    global _STORAGE_SINGLETON
    if _STORAGE_SINGLETON is not None:
        return _STORAGE_SINGLETON
    backend = os.environ.get("BIOFORGE_STORAGE_BACKEND", "local").strip().lower()
    if backend == "minio":
        _STORAGE_SINGLETON = MinioStorage()
    else:
        root = os.environ.get("BIOFORGE_STORAGE_ROOT", "/data/storage")
        _STORAGE_SINGLETON = LocalStorage(root_dir=root)
    return _STORAGE_SINGLETON


def reset_storage() -> None:
    """Reset the singleton — used by tests that need to swap backends or roots."""
    global _STORAGE_SINGLETON
    _STORAGE_SINGLETON = None
