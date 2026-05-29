"""Fetch + verify + extract the DeepCRISPR weights on demand.

Mirrors the inDelphi fetcher's design **minus the consent gate** — DeepCRISPR is
Apache-2.0, so we may fetch and even redistribute with attribution. Goals:

1. **No bundled weights.** Nothing enters our git history. `ensure_available()`
   downloads into `deepcrispr_data_dir` (or `~/.bioforge/data/deepcrispr/`).
2. **Reproducible by commit.** Every download is pinned to
   `settings.deepcrispr_upstream_commit`; one subdir per commit.
3. **Trust-on-first-use checksums.** The first successful download pins a
   sha256 in `pinned_hashes.json`; later loads verify against it.
4. **LFS-aware.** If the upstream tarball is tracked by Git LFS,
   raw.githubusercontent.com returns a tiny pointer file instead of the archive.
   We detect that and raise with the LFS media URL rather than try to extract a
   pointer.
5. **Testable without network.** Downloads go through an injectable `download_fn`.

This module only fetches. The subprocess that actually runs TensorFlow 1.x lives
behind `runner.py`; orchestration is in `inference.py`.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bioforge.config import Settings
from bioforge.config import settings as _default_settings
from bioforge.tools.sequence.models.deepcrispr.manifest import (
    SUPPORTED_ONTARGET_MODELS,
    OnTargetModel,
    lfs_media_url,
    raw_url,
    required_archive,
)

DownloadFn = Callable[[str], bytes]

# An LFS pointer file is small UTF-8 text beginning with this line.
_LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/v1"


class DeepCRISPRUnavailable(Exception):
    """Raised when the out-of-process legacy runtime is not usable.

    Carries actionable setup instructions (build the Docker image or the conda
    env) so the tool boundary can surface them to the agent.
    """


class DeepCRISPRFetchError(Exception):
    """Raised for any failure during fetch/extract — download, IO, hash mismatch,
    LFS pointer, or a malformed archive — so the tool surfaces a clean error."""


@dataclass(frozen=True)
class DeepCRISPRPaths:
    """Resolved local paths for one extracted DeepCRISPR model.

    `model_dir` is the directory handed to DeepCRISPR's `DCModelOntar` as
    `on_target_model_dir`. `archive_path` is the downloaded tarball.
    """

    data_dir: Path
    model_dir: Path
    archive_path: Path


def _default_data_dir() -> Path:
    """`~/.bioforge/data/deepcrispr/`. Created on first use."""
    return Path(os.path.expanduser("~")) / ".bioforge" / "data" / "deepcrispr"


def _resolve_data_dir(s: Settings) -> Path:
    if s.deepcrispr_data_dir:
        return Path(s.deepcrispr_data_dir).expanduser().resolve()
    return _default_data_dir()


def _commit_root(data_dir: Path, commit_sha: str) -> Path:
    return data_dir / commit_sha


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hashes_path(commit_root: Path) -> Path:
    return commit_root / "pinned_hashes.json"


def _load_pinned_hashes(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise DeepCRISPRFetchError(
            f"pinned_hashes.json at {path} is unreadable ({e}). Delete it to force a re-download."
        ) from e


def _write_pinned_hashes(path: Path, hashes: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(hashes, f, indent=2, sort_keys=True)
    tmp.replace(path)


def _require_model_supported(model: OnTargetModel) -> None:
    if model not in SUPPORTED_ONTARGET_MODELS:
        raise DeepCRISPRFetchError(
            f"Model {model!r} is not in the supported set {list(SUPPORTED_ONTARGET_MODELS)}. "
            "Only the sequence-only on-target regression model is wired up."
        )


def _httpx_download(url: str) -> bytes:
    """Default `download_fn`. Lazy-imports httpx; tests inject their own."""
    import httpx

    try:
        resp = httpx.get(url, timeout=120.0, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise DeepCRISPRFetchError(
            f"Failed to download {url}: {e}. Check connectivity and that the pinned commit exists."
        ) from e
    return resp.content


def _looks_like_lfs_pointer(blob: bytes) -> bool:
    # Pointer files are small and start with the LFS spec line.
    return len(blob) < 1024 and blob.lstrip().startswith(_LFS_POINTER_PREFIX)


def _safe_extract(blob: bytes, dest: Path) -> None:
    """Extract a .tar.gz into `dest`, refusing any member that escapes `dest`.

    Hand-rolled rather than `extractall(filter="data")` because that filter is
    only guaranteed on Python >= 3.12; this stays correct on 3.11.
    """
    dest.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest.resolve()
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            for member in tar.getmembers():
                target = (dest / member.name).resolve()
                if dest_resolved != target and dest_resolved not in target.parents:
                    raise DeepCRISPRFetchError(f"Refusing to extract member {member.name!r}: path escapes {dest}.")
                if member.issym() or member.islnk():
                    raise DeepCRISPRFetchError(f"Refusing to extract link member {member.name!r} from the archive.")
            tar.extractall(dest)  # noqa: S202 — members validated above
    except tarfile.TarError as e:
        raise DeepCRISPRFetchError(
            f"Could not extract DeepCRISPR archive into {dest}: {e}. The download may be "
            "corrupt or an LFS pointer; delete the data dir and retry."
        ) from e


def ensure_available(
    model: OnTargetModel = "ontar_cnn_reg_seq",
    *,
    settings: Settings | None = None,
    download_fn: DownloadFn | None = None,
) -> DeepCRISPRPaths:
    """Make sure the DeepCRISPR weights for `model` are present + extracted locally.

    Steps: resolve the commit-scoped data dir; download the tarball if absent
    (verifying/pinning its sha256, and rejecting an LFS pointer); extract it; and
    return the paths. After a successful first run this function is offline.

    `download_fn` is injectable for tests. Raises `DeepCRISPRFetchError` on any
    fetch/extract/verify failure. Does NOT touch the network beyond the single
    archive download, and never requires consent (Apache-2.0).
    """
    s = settings if settings is not None else _default_settings
    fetch = download_fn if download_fn is not None else _httpx_download

    _require_model_supported(model)

    arc = required_archive(model)
    data_dir = _resolve_data_dir(s)
    commit_root = _commit_root(data_dir, s.deepcrispr_upstream_commit)
    archive_path = commit_root / arc.local_archive_relpath
    model_dir = commit_root / arc.extract_dirname
    hashes_path = _hashes_path(commit_root)
    pinned = _load_pinned_hashes(hashes_path)
    pinned_updated = False

    archive_path.parent.mkdir(parents=True, exist_ok=True)

    if archive_path.exists():
        actual = _sha256(archive_path.read_bytes())
        expected = pinned.get(arc.local_archive_relpath)
        if expected is None:
            pinned[arc.local_archive_relpath] = actual
            pinned_updated = True
        elif actual != expected:
            raise DeepCRISPRFetchError(
                f"Hash mismatch for {arc.local_archive_relpath}: pinned {expected!r}, on disk "
                f"{actual!r}. The file was changed or the commit pin moved without clearing the "
                f"data dir. Delete {commit_root} and re-run to bootstrap fresh."
            )
    else:
        url = raw_url(s.deepcrispr_upstream_commit, arc.upstream_relpath)
        blob = fetch(url)
        if _looks_like_lfs_pointer(blob):
            raise DeepCRISPRFetchError(
                f"{arc.upstream_relpath} came back as a Git-LFS pointer, not the archive. "
                f"Fetch the real media from {lfs_media_url(s.deepcrispr_upstream_commit, arc.upstream_relpath)} "
                "(or `git lfs pull` a local clone) and place it at "
                f"{archive_path}, then re-run."
            )
        actual = _sha256(blob)
        expected = pinned.get(arc.local_archive_relpath)
        if expected is not None and actual != expected:
            raise DeepCRISPRFetchError(
                f"Downloaded {arc.upstream_relpath} but its sha256 {actual!r} does not match the "
                f"pinned {expected!r} for commit {s.deepcrispr_upstream_commit}. Upstream may have "
                "force-pushed; verify the commit pin and clear the data dir."
            )
        archive_path.write_bytes(blob)
        if expected is None:
            pinned[arc.local_archive_relpath] = actual
            pinned_updated = True

    # Extract if the model dir isn't already materialized.
    if not model_dir.exists() or not any(model_dir.iterdir()):
        _safe_extract(archive_path.read_bytes(), model_dir)

    if pinned_updated:
        _write_pinned_hashes(hashes_path, pinned)

    return DeepCRISPRPaths(data_dir=commit_root, model_dir=model_dir, archive_path=archive_path)
