"""Fetch + verify the upstream inDelphi sources on demand.

Design goals (in priority order):

1. **No silent network**. The fetcher refuses to touch the network until the
   user sets `BIOFORGE_INDELPHI_CONSENT_NONCOMMERCIAL=true` AFTER reading
   `LICENSE_NOTICE.md`. The flag is the sole consent signal.
2. **No bundled weights**. Nothing inDelphi-licensed ever enters our git
   history. `ensure_available()` downloads into the user-configured
   `indelphi_data_dir` (or `~/.bioforge/data/indelphi/` if unset).
3. **Reproducible by commit**. Every download is pinned to
   `settings.indelphi_upstream_commit`. Re-running with the same commit pin
   produces bit-identical local files.
4. **Trust-on-first-use checksums**. The first successful download writes
   a `pinned_hashes.json` keyed by `(commit, relpath) -> sha256`. Subsequent
   loads verify against it. A commit-pin change forces a fresh TOFU pass.
5. **Testable without network**. Downloads go through an injectable
   `download_fn`. Production passes one backed by httpx; tests pass a fake.

This module ships ONLY the fetcher. Inference (`predict()`) is the next slice
and lives in `inference.py`.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bioforge.config import Settings
from bioforge.config import settings as _default_settings
from bioforge.tools.sequence.models.indelphi.manifest import (
    SUPPORTED_CELLTYPES,
    CellType,
    raw_url,
    required_files,
)

DownloadFn = Callable[[str], bytes]


class InDelphiUnavailable(Exception):
    """Raised when the optional inDelphi dependencies aren't installed.

    Message tells the user how to opt in (`pip install -e .[indelphi]`).
    """


class InDelphiConsentRequired(Exception):
    """Raised when consent flag is unset and we'd otherwise have to network.

    Caught at the tool boundary and surfaced to the agent with the consent
    instructions so the user can decide whether to opt in.
    """


class InDelphiFetchError(Exception):
    """Raised for any failure during fetch — download error, IO error, hash
    mismatch — so the tool can surface a clean error rather than a stack trace.
    """


@dataclass(frozen=True)
class InDelphiPaths:
    """Resolved local paths for an inDelphi installation.

    `data_dir` is the root the upstream layout lives under. `model_dir` is
    the sklearn-version subdir that inDelphi.init_model loads from. `script`
    is the top-level inDelphi.py.
    """

    data_dir: Path
    model_dir: Path
    script: Path


def _default_data_dir() -> Path:
    """`~/.bioforge/data/indelphi/`. Created on first use."""
    home = Path(os.path.expanduser("~"))
    return home / ".bioforge" / "data" / "indelphi"


def _resolve_data_dir(s: Settings) -> Path:
    if s.indelphi_data_dir:
        return Path(s.indelphi_data_dir).expanduser().resolve()
    return _default_data_dir()


def _commit_root(data_dir: Path, commit_sha: str) -> Path:
    """One subdir per pinned commit so flipping the commit pin is a clean
    re-bootstrap rather than an in-place overwrite."""
    return data_dir / commit_sha


def _hashes_path(commit_root: Path) -> Path:
    return commit_root / "pinned_hashes.json"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_pinned_hashes(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise InDelphiFetchError(
            f"pinned_hashes.json at {path} is unreadable ({e}). Delete the file "
            f"to force a re-download, or restore it from a backup."
        ) from e


def _write_pinned_hashes(path: Path, hashes: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(hashes, f, indent=2, sort_keys=True)
    tmp.replace(path)


def _require_consent(s: Settings) -> None:
    if s.indelphi_consent_noncommercial:
        return
    raise InDelphiConsentRequired(
        "inDelphi is licensed for NON-COMMERCIAL RESEARCH USE ONLY. Read "
        "backend/src/bioforge/tools/sequence/models/indelphi/LICENSE_NOTICE.md, "
        "then set BIOFORGE_INDELPHI_CONSENT_NONCOMMERCIAL=true to opt in. "
        "If your usage may become commercial, do not enable inDelphi — use the "
        "rule_of_thumb model on edit_outcome instead."
    )


def _require_celltype_supported(celltype: CellType) -> None:
    if celltype not in SUPPORTED_CELLTYPES:
        raise InDelphiFetchError(
            f"Cell type {celltype!r} not in the shipped manifest "
            f"({list(SUPPORTED_CELLTYPES)}). Add it to manifest.SUPPORTED_CELLTYPES "
            "and the corresponding *_<celltype>.pkl entries are auto-derived."
        )


def _httpx_download(url: str) -> bytes:
    """Default `download_fn`. Lazy-imports httpx so a test can run without it
    if the test injects its own download_fn (we still keep httpx in the core
    deps, but lazy-import keeps import-time clean)."""
    import httpx

    try:
        resp = httpx.get(url, timeout=60.0, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise InDelphiFetchError(
            f"Failed to download {url}: {e}. Check network connectivity and "
            "that the pinned commit still exists upstream."
        ) from e
    return resp.content


def ensure_available(
    celltype: CellType = "mESC",
    *,
    settings: Settings | None = None,
    download_fn: DownloadFn | None = None,
) -> InDelphiPaths:
    """Make sure the upstream inDelphi files are present locally.

    Steps:
      1. Verify the user has consented (flag set) — else `InDelphiConsentRequired`.
      2. Resolve the data dir + commit-scoped root.
      3. For each required file: if absent, download; if present, verify
         sha256 against `pinned_hashes.json`. On first download (TOFU), the
         observed hash is pinned.
      4. Return `InDelphiPaths` pointing at the materialized layout.

    Network calls happen only inside step 3 and only for missing files. After
    a successful first run, this function is offline.

    `download_fn` is injectable for tests. Default uses httpx.
    """
    s = settings if settings is not None else _default_settings
    fetch = download_fn if download_fn is not None else _httpx_download

    _require_consent(s)
    _require_celltype_supported(celltype)

    data_dir = _resolve_data_dir(s)
    commit_root = _commit_root(data_dir, s.indelphi_upstream_commit)
    hashes_path = _hashes_path(commit_root)
    pinned = _load_pinned_hashes(hashes_path)
    pinned_updated = False

    for spec in required_files(celltype):
        local_path = commit_root / spec.local_relpath
        local_path.parent.mkdir(parents=True, exist_ok=True)

        if local_path.exists():
            actual = _sha256(local_path.read_bytes())
            expected = pinned.get(spec.local_relpath)
            if expected is None:
                # File present but never pinned — could be a leftover from an
                # aborted run. Pin it now (TOFU).
                pinned[spec.local_relpath] = actual
                pinned_updated = True
            elif actual != expected:
                raise InDelphiFetchError(
                    f"Hash mismatch for {spec.local_relpath}: pinned {expected!r}, "
                    f"on disk {actual!r}. Either the file was tampered with or the "
                    f"commit pin was changed without clearing the data dir. Delete "
                    f"{commit_root} and re-run to bootstrap fresh."
                )
            continue

        # Missing — fetch.
        url = raw_url(s.indelphi_upstream_commit, spec.upstream_relpath)
        blob = fetch(url)
        actual = _sha256(blob)
        expected = pinned.get(spec.local_relpath)
        if expected is not None and actual != expected:
            raise InDelphiFetchError(
                f"Downloaded {spec.upstream_relpath} but its sha256 {actual!r} does not "
                f"match the previously pinned value {expected!r} for commit "
                f"{s.indelphi_upstream_commit}. Upstream may have force-pushed; "
                "verify the commit pin and clear the data dir to re-bootstrap."
            )
        local_path.write_bytes(blob)
        if expected is None:
            pinned[spec.local_relpath] = actual
            pinned_updated = True

    if pinned_updated:
        _write_pinned_hashes(hashes_path, pinned)

    from bioforge.tools.sequence.models.indelphi.manifest import model_dir_relpath

    return InDelphiPaths(
        data_dir=commit_root,
        model_dir=commit_root / model_dir_relpath(),
        script=commit_root / "inDelphi.py",
    )
