"""Tests for the inDelphi fetcher + consent gate.

These tests do NOT hit the network. The fetcher accepts an injected
`download_fn`; every test supplies a fake that returns canned bytes. A separate
@pytest.mark.online test (in a follow-up slice) will exercise the real httpx
backend.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bioforge.config import Settings
from bioforge.tools.sequence.models.indelphi import (
    InDelphiConsentRequired,
    InDelphiFetchError,
    ensure_available,
)
from bioforge.tools.sequence.models.indelphi.manifest import (
    raw_url,
    required_files,
)

# --- Test helpers --------------------------------------------------------------------


def _make_settings(*, tmp_path: Path, consent: bool = True, commit: str = "abc1234") -> Settings:
    """Build an isolated Settings — never touch the module-level singleton."""
    s = Settings()  # reads env / .env but we override the inDelphi-relevant fields
    s.indelphi_consent_noncommercial = consent
    s.indelphi_data_dir = str(tmp_path)
    s.indelphi_upstream_commit = commit
    return s


class FakeDownloader:
    """Canned-response download_fn that records every URL it was asked for.

    `payloads` maps a substring matched in the URL to the bytes returned. The
    first matching substring wins; raises if none match (so a missing canned
    response surfaces as a clear test failure rather than a hang).
    """

    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        for needle, blob in self.payloads.items():
            if needle in url:
                return blob
        raise AssertionError(f"FakeDownloader has no canned response for {url!r}")


def _canned_payloads_for(celltype: str = "mESC") -> dict[str, bytes]:
    """Map every required upstream relpath to deterministic dummy bytes."""
    return {f.upstream_relpath: f"BLOB::{f.upstream_relpath}".encode() for f in required_files(celltype)}


# --- Consent gate --------------------------------------------------------------------


def test_consent_required_raises_without_flag(tmp_path: Path) -> None:
    s = _make_settings(tmp_path=tmp_path, consent=False)
    downloader = FakeDownloader(_canned_payloads_for())

    with pytest.raises(InDelphiConsentRequired) as exc:
        ensure_available(settings=s, download_fn=downloader)

    # Error message must reference both the consent flag and the license notice
    # — that's what makes it actionable for the agent / user.
    msg = str(exc.value)
    assert "BIOFORGE_INDELPHI_CONSENT_NONCOMMERCIAL" in msg
    assert "LICENSE_NOTICE.md" in msg
    # No network calls happened.
    assert downloader.urls == []


def test_consent_gate_blocks_before_any_filesystem_writes(tmp_path: Path) -> None:
    s = _make_settings(tmp_path=tmp_path, consent=False)
    downloader = FakeDownloader(_canned_payloads_for())

    with pytest.raises(InDelphiConsentRequired):
        ensure_available(settings=s, download_fn=downloader)

    # The commit-scoped subdir must not exist — we shouldn't even prepare for a download.
    assert list(tmp_path.iterdir()) == []


# --- Happy path ----------------------------------------------------------------------


def test_first_run_downloads_all_files_and_pins_hashes(tmp_path: Path) -> None:
    s = _make_settings(tmp_path=tmp_path)
    downloader = FakeDownloader(_canned_payloads_for())

    paths = ensure_available(settings=s, download_fn=downloader)

    # Every required upstream relpath was downloaded exactly once.
    expected_urls = {raw_url(s.indelphi_upstream_commit, f.upstream_relpath) for f in required_files("mESC")}
    assert set(downloader.urls) == expected_urls
    assert len(downloader.urls) == len(expected_urls)  # no dupes

    # Files landed under the commit-scoped subdir.
    assert paths.data_dir == tmp_path / s.indelphi_upstream_commit
    for spec in required_files("mESC"):
        assert (paths.data_dir / spec.local_relpath).exists()

    # The pinned_hashes manifest was written and has one entry per required file.
    hashes_file = paths.data_dir / "pinned_hashes.json"
    assert hashes_file.exists()
    pinned = json.loads(hashes_file.read_text())
    assert set(pinned.keys()) == {f.local_relpath for f in required_files("mESC")}


def test_second_run_is_offline_when_files_present(tmp_path: Path) -> None:
    s = _make_settings(tmp_path=tmp_path)

    # First call: downloads everything.
    first = FakeDownloader(_canned_payloads_for())
    ensure_available(settings=s, download_fn=first)
    assert len(first.urls) > 0

    # Second call with a downloader that would explode on any URL — proves
    # the second call is purely a local hash-verify operation.
    def explode(url: str) -> bytes:
        raise AssertionError(f"Second run unexpectedly tried to download {url!r}")

    ensure_available(settings=s, download_fn=explode)


def test_partial_state_resumes_only_missing_files(tmp_path: Path) -> None:
    s = _make_settings(tmp_path=tmp_path)

    # First run downloads everything.
    first = FakeDownloader(_canned_payloads_for())
    paths = ensure_available(settings=s, download_fn=first)

    # Simulate an interrupted state: delete one of the files but keep the
    # pinned_hashes manifest. Second run should re-fetch only that file.
    missing = required_files("mESC")[1]
    (paths.data_dir / missing.local_relpath).unlink()

    second = FakeDownloader(_canned_payloads_for())
    ensure_available(settings=s, download_fn=second)

    assert second.urls == [raw_url(s.indelphi_upstream_commit, missing.upstream_relpath)]


# --- Failure modes -------------------------------------------------------------------


def test_hash_mismatch_after_download_raises(tmp_path: Path) -> None:
    """If a file is already pinned (e.g. via a manual edit) and a new download
    produces different bytes, refuse — protects against silent corruption."""
    s = _make_settings(tmp_path=tmp_path)

    # Bootstrap one file's pinned hash with a value that won't match what the
    # downloader returns.
    commit_root = tmp_path / s.indelphi_upstream_commit
    commit_root.mkdir(parents=True)
    target = required_files("mESC")[0]
    (commit_root / "pinned_hashes.json").write_text(json.dumps({target.local_relpath: "0" * 64}, indent=2))

    downloader = FakeDownloader(_canned_payloads_for())
    with pytest.raises(InDelphiFetchError) as exc:
        ensure_available(settings=s, download_fn=downloader)
    assert "sha256" in str(exc.value).lower()


def test_on_disk_hash_mismatch_raises(tmp_path: Path) -> None:
    """If a file is present on disk AND pinned but its bytes don't match the
    pin (someone edited the file), refuse rather than silently proceed."""
    s = _make_settings(tmp_path=tmp_path)

    # First, fully bootstrap.
    downloader = FakeDownloader(_canned_payloads_for())
    paths = ensure_available(settings=s, download_fn=downloader)

    # Corrupt one of the files in place.
    target = required_files("mESC")[2]
    (paths.data_dir / target.local_relpath).write_bytes(b"CORRUPTED")

    # Now re-run — the on-disk file no longer matches its pinned hash.
    def explode(url: str) -> bytes:
        raise AssertionError(f"Should not download {url!r}; this is a hash-verify failure")

    with pytest.raises(InDelphiFetchError) as exc:
        ensure_available(settings=s, download_fn=explode)
    assert "hash mismatch" in str(exc.value).lower() or "sha256" in str(exc.value).lower()


def test_unsupported_celltype_raises(tmp_path: Path) -> None:
    s = _make_settings(tmp_path=tmp_path)
    downloader = FakeDownloader({})

    with pytest.raises(InDelphiFetchError) as exc:
        # type: ignore[arg-type] — exercising the unsupported path on purpose
        ensure_available(celltype="HEK293T", settings=s, download_fn=downloader)  # type: ignore[arg-type]
    assert "manifest.SUPPORTED_CELLTYPES" in str(exc.value)
    # No downloads happened.
    assert downloader.urls == []


def test_commit_pin_change_isolates_directories(tmp_path: Path) -> None:
    """Two different commit pins should produce two parallel local trees, so
    flipping the pin is a clean re-bootstrap rather than a silent overwrite."""
    s = _make_settings(tmp_path=tmp_path, commit="commit_aaa")
    ensure_available(settings=s, download_fn=FakeDownloader(_canned_payloads_for()))

    s.indelphi_upstream_commit = "commit_bbb"
    ensure_available(settings=s, download_fn=FakeDownloader(_canned_payloads_for()))

    assert (tmp_path / "commit_aaa" / "pinned_hashes.json").exists()
    assert (tmp_path / "commit_bbb" / "pinned_hashes.json").exists()


def test_corrupt_pinned_hashes_file_raises(tmp_path: Path) -> None:
    s = _make_settings(tmp_path=tmp_path)
    commit_root = tmp_path / s.indelphi_upstream_commit
    commit_root.mkdir(parents=True)
    (commit_root / "pinned_hashes.json").write_text("not-valid-json {")

    with pytest.raises(InDelphiFetchError) as exc:
        ensure_available(settings=s, download_fn=FakeDownloader(_canned_payloads_for()))
    assert "pinned_hashes.json" in str(exc.value)
