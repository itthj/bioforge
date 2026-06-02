"""section-13 edit-outcome benchmark -- FORECasT observed-profile loader unit tests.

No network, no Docker. The fetch-on-first-use loader is exercised with an injected download_fn +
a monkeypatched fixture sample (so we never touch the real ~27 MB figshare file); the ZIP /
`@@@`-block parser is checked against hand-built fixtures incl. normalization, the wild-type `-`
drop, and the malformed-structure refusals. The REAL K562 x FORECasT run is a `-m docker -m online`
e2e (next slice).
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest
from bioforge.benchmarks import forecast_profiles
from bioforge.benchmarks.forecast_profiles import (
    ForecastProfilesConsentRequired,
    ForecastProfilesFetchError,
    ForecastProfilesUnknown,
    ObservedSpec,
    load_observed,
    parse_observed_profiles,
)
from bioforge.config import settings as _settings_singleton


def _make_zip(members: dict[str, str]) -> bytes:
    """Build a deterministic ZIP from {member_path: text_content}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in members.items():
            zi = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            zf.writestr(zi, text)
    return buf.getvalue()


# Two members, three oligos. Oligo1: D2=6, I1=2 (+ a WT `-` line that must be dropped) -> total 8,
# dist {D2:0.75, I1:0.25}. Oligo2: D1=3 -> {D1:1.0}. Oligo3 (other member): one label.
_MEMBER_A = (
    "@@@Oligo1\n"
    "D2_L-3R0\t6\tACGTACGTAC\n"
    "I1_L-2C1R0\t2\tACGTAACGTAC\n"
    "-\t1000\tACGTACGTACG\n"
    "@@@Oligo2\n"
    "D1_L-2R0\t3\tACGTCGTAC\n"
)
_MEMBER_B = "@@@Oligo3\nD3_L-4R0\t5\tACGTGTAC\n"
_FIXTURE_ZIP = _make_zip(
    {
        "S/Oligos_1/a_processedindels.txt": _MEMBER_A,
        "S/Oligos_1/b_processedindels.txt": _MEMBER_B,
        "S/Oligos_1/notes.txt": "ignored -- wrong suffix\n",
    }
)


def _register_fixture(monkeypatch: pytest.MonkeyPatch, *, n_oligos: int = 3) -> ObservedSpec:
    spec = ObservedSpec(
        name="fixture",
        download_url="https://example.invalid/files/0",
        cache_filename="fixture.zip",
        expected_sha256=hashlib.sha256(_FIXTURE_ZIP).hexdigest(),
        n_oligos=n_oligos,
        sample_label="fixture sample",
        citation="fixture citation",
        notes="fixture",
    )
    monkeypatch.setitem(forecast_profiles.OBSERVED, "fixture", spec)
    return spec


def _settings(tmp_path: Path, *, consent: bool = True):
    return _settings_singleton.model_copy(
        update={"forecast_profiles_dir": str(tmp_path), "forecast_profiles_consent": consent}
    )


class _Downloader:
    def __init__(self, blob: bytes) -> None:
        self.blob = blob
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        return self.blob


def _explode(url: str) -> bytes:
    raise AssertionError(f"unexpected download of {url!r}")


# --- loader: consent gate + acquisition paths ---------------------------------------------------


def test_consent_required_blocks_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register_fixture(monkeypatch)
    s = _settings(tmp_path, consent=False)

    with pytest.raises(ForecastProfilesConsentRequired) as exc:
        load_observed("fixture", settings=s, download_fn=_explode)

    msg = str(exc.value)
    assert "BIOFORGE_FORECAST_PROFILES_CONSENT" in msg
    assert "local_path" in msg
    assert list(tmp_path.iterdir()) == []  # nothing prepared, no bytes touched


def test_fetch_verifies_caches_then_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register_fixture(monkeypatch)
    s = _settings(tmp_path)

    dl = _Downloader(_FIXTURE_ZIP)
    loaded = load_observed("fixture", settings=s, download_fn=dl)
    assert len(loaded.profiles) == 3
    assert loaded.source.startswith("fetch:")
    assert loaded.sha256 == hashlib.sha256(_FIXTURE_ZIP).hexdigest()
    assert len(dl.urls) == 1
    assert (tmp_path / "fixture.zip").exists()

    # Oligo1 distribution: WT dropped, normalized over the two real labels.
    o1 = loaded.profiles["Oligo1"]
    assert o1.total_reads == 8
    assert o1.distribution["D2_L-3R0"] == pytest.approx(0.75)
    assert o1.distribution["I1_L-2C1R0"] == pytest.approx(0.25)
    assert "-" not in o1.distribution
    assert sum(o1.distribution.values()) == pytest.approx(1.0)
    assert set(loaded.profiles) == {"Oligo1", "Oligo2", "Oligo3"}

    # Second load reads the cache -- a downloader that would explode proves no network.
    again = load_observed("fixture", settings=s, download_fn=_explode)
    assert again.source.startswith("cache:")


def test_local_path_bypasses_consent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register_fixture(monkeypatch)
    supplied = tmp_path / "supplied.zip"
    supplied.write_bytes(_FIXTURE_ZIP)
    s = _settings(tmp_path, consent=False)

    loaded = load_observed("fixture", settings=s, download_fn=_explode, local_path=str(supplied))
    assert len(loaded.profiles) == 3
    assert loaded.source.startswith("local:")


def test_sha256_mismatch_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register_fixture(monkeypatch)
    s = _settings(tmp_path)
    tampered = _FIXTURE_ZIP + b"\x00"  # valid-ish bytes, wrong hash

    with pytest.raises(ForecastProfilesFetchError) as exc:
        load_observed("fixture", settings=s, download_fn=_Downloader(tampered))
    assert "sha256" in str(exc.value).lower()
    assert not (tmp_path / "fixture.zip").exists()  # bad bytes never cached


def test_oligo_count_mismatch_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register_fixture(monkeypatch, n_oligos=99)  # zip has 3 oligos; spec claims 99
    s = _settings(tmp_path)

    with pytest.raises(ForecastProfilesFetchError) as exc:
        load_observed("fixture", settings=s, download_fn=_Downloader(_FIXTURE_ZIP))
    assert "oligo" in str(exc.value).lower()


def test_unknown_sample_raises(tmp_path: Path) -> None:
    with pytest.raises(ForecastProfilesUnknown):
        load_observed("does-not-exist", settings=_settings(tmp_path))


# --- parser: normalization, WT drop, empty, refusals --------------------------------------------


def test_parse_drops_wt_and_handles_empty_distribution() -> None:
    z = _make_zip(
        {
            "x/a_processedindels.txt": "@@@OnlyWT\n-\t1000\tACGT\n@@@HasReads\nD1_L-2R0\t4\tACGT\n",
        }
    )
    profiles = parse_observed_profiles(z)
    assert profiles["OnlyWT"].total_reads == 0
    assert profiles["OnlyWT"].distribution == {}  # no edited reads -> empty, not fabricated
    assert profiles["HasReads"].distribution == {"D1_L-2R0": pytest.approx(1.0)}


def test_parse_merges_repeated_label_counts() -> None:
    z = _make_zip({"x/a_processedindels.txt": "@@@O\nD1_L-2R0\t2\tA\nD1_L-2R0\t3\tA\n"})
    profiles = parse_observed_profiles(z)
    assert profiles["O"].total_reads == 5
    assert profiles["O"].distribution == {"D1_L-2R0": pytest.approx(1.0)}


def test_parse_rejects_row_before_header() -> None:
    z = _make_zip({"x/a_processedindels.txt": "D1_L-2R0\t3\tA\n@@@O\nD2_L-3R0\t1\tA\n"})
    with pytest.raises(ForecastProfilesFetchError) as exc:
        parse_observed_profiles(z)
    assert "before any" in str(exc.value).lower()


def test_parse_rejects_nonnumeric_count() -> None:
    z = _make_zip({"x/a_processedindels.txt": "@@@O\nD1_L-2R0\tnope\tA\n"})
    with pytest.raises(ForecastProfilesFetchError) as exc:
        parse_observed_profiles(z)
    assert "non-numeric" in str(exc.value).lower()


def test_parse_rejects_duplicate_oligo_across_members() -> None:
    z = _make_zip(
        {
            "x/a_processedindels.txt": "@@@Odup\nD1_L-2R0\t3\tA\n",
            "x/b_processedindels.txt": "@@@Odup\nD2_L-3R0\t2\tA\n",
        }
    )
    with pytest.raises(ForecastProfilesFetchError) as exc:
        parse_observed_profiles(z)
    assert "duplicate" in str(exc.value).lower()


def test_parse_rejects_non_zip() -> None:
    with pytest.raises(ForecastProfilesFetchError) as exc:
        parse_observed_profiles(b"not a zip at all")
    assert "zip" in str(exc.value).lower()


def test_parse_rejects_zip_without_profile_members() -> None:
    z = _make_zip({"x/readme.txt": "no profiles here\n"})
    with pytest.raises(ForecastProfilesFetchError) as exc:
        parse_observed_profiles(z)
    assert "members" in str(exc.value).lower()
