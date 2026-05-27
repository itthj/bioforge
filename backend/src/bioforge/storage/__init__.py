"""Object storage adapter — Phase 1 large-output persistence.

Some tool outputs are too large to thread through the agent's LLM context
(BLAST XML for big queries, structure files for ribosomes, future RNA-seq
matrices). The pattern is: tool writes the heavy result to storage, returns
a pointer (project_id + key), agent surfaces the pointer + a digest.

The Storage interface lets the deployment swap implementations without
touching the tools:
  - LocalStorage (default): writes under /data/storage/<project_id>/.
    Single-user local; no infra dependency.
  - MinioStorage: writes to S3-compatible object store, prefixed by
    project_id. Enabled by BIOFORGE_STORAGE_BACKEND=minio.

Project-id prefixing is the isolation boundary by design — same rule as the
DB row-level project_id from Phase 0. Multi-user auth on top of this is a
Phase 6+ concern; the prefix is already the right boundary for it.
"""

from bioforge.storage.adapter import (
    LocalStorage,
    MinioStorage,
    ObjectMetadata,
    Storage,
    StorageError,
    get_storage,
)

__all__ = [
    "LocalStorage",
    "MinioStorage",
    "ObjectMetadata",
    "Storage",
    "StorageError",
    "get_storage",
]
