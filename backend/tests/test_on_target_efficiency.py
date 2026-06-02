"""§13 on-target efficiency benchmark -- unit tests (no network, no Docker).

The fetch-on-first-use loader is exercised with an injected download_fn + a monkeypatched
fixture dataset (so we never touch the real unlicensed file); the numpy correlation is checked
against hand-computed values including ties; and the orchestration runs with an injected
predict_fn and asserts the HONESTY labels (leakage 'unknown', cross-dataset). The REAL DeepCRISPR
x Chari-2015 run is a `-m docker` e2e in test_models_docker_e2e.py.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import pytest
from bioforge.benchmarks import effdata
from bioforge.benchmarks.effdata import (
    EffDataConsentRequired,
    EffDataFetchError,
    EffDataset,
    EffDataUnknown,
    load_dataset,
    parse_tab,
)
from bioforge.benchmarks.on_target_efficiency import (
    _LEAKAGE,
    average_ranks,
    pearson_r,
    run_on_target_efficiency,
    spearman_rho,
)
from bioforge.config import settings as _settings_singleton

# 4 data rows; rows 2 & 3 TIE on modFreq (0.50) to exercise tie-aware ranking, and row 4 exceeds
# 1.0 (modFreq is not a [0,1] probability). Each seq is a real 23-mer ending in NGG.
_FIXTURE_TAB = (
    b"guide\tseq\tmodFreq\n"
    b"G1_chr1_100\tAGCGTACCCCCAGGTCTTGCAGG\t0.10\n"
    b"G2_chr2_200\tCCAATTGCCTTCAGATCAATAGG\t0.50\n"
    b"G3_chr3_300\tACAGGGCGCTCCATATTCGCAGG\t0.50\n"
    b"G4_chr4_400\tACGTACGTACGTACGTACGTTGG\t1.30\n"
)


def _register_fixture(monkeypatch: pytest.MonkeyPatch, *, n_rows: int = 4) -> EffDataset:
    """Register a fixture dataset whose expected_sha256 matches the canned bytes."""
    spec = EffDataset(
        name="fixture",
        upstream_relpath="effData/fixture.tab",
        expected_sha256=hashlib.sha256(_FIXTURE_TAB).hexdigest(),
        n_rows=n_rows,
        citation="fixture citation",
        notes="fixture",
    )
    monkeypatch.setitem(effdata.DATASETS, "fixture", spec)
    return spec


def _settings(tmp_path: Path, *, consent: bool = True):
    return _settings_singleton.model_copy(
        update={
            "crispor_effdata_dir": str(tmp_path),
            "crispor_effdata_consent": consent,
            "crispor_effdata_commit": "testcommit",
        }
    )


class _Downloader:
    """Canned download_fn that records every URL it was asked for."""

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

    with pytest.raises(EffDataConsentRequired) as exc:
        load_dataset("fixture", settings=s, download_fn=_explode)

    msg = str(exc.value)
    assert "BIOFORGE_CRISPOR_EFFDATA_CONSENT" in msg
    assert "local_path" in msg  # the local-file alternative is surfaced
    assert list(tmp_path.iterdir()) == []  # no directory prepared, no bytes touched


def test_fetch_verifies_caches_then_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register_fixture(monkeypatch)
    s = _settings(tmp_path)

    dl = _Downloader(_FIXTURE_TAB)
    loaded = load_dataset("fixture", settings=s, download_fn=dl)
    assert len(loaded.rows) == 4
    assert loaded.source.startswith("fetch:")
    assert loaded.sha256 == hashlib.sha256(_FIXTURE_TAB).hexdigest()
    assert loaded.rows[0].guide_name == "G1_chr1_100"
    assert loaded.rows[3].mod_freq == 1.30  # >1.0 preserved verbatim
    assert len(dl.urls) == 1
    assert (tmp_path / "testcommit" / "fixture.tab").exists()

    # Second load reads the cache -- a downloader that would explode proves no network.
    again = load_dataset("fixture", settings=s, download_fn=_explode)
    assert again.source.startswith("cache:")


def test_local_path_bypasses_consent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register_fixture(monkeypatch)
    supplied = tmp_path / "supplied.tab"
    supplied.write_bytes(_FIXTURE_TAB)
    s = _settings(tmp_path, consent=False)  # no consent flag, but a supplied file needs none

    loaded = load_dataset("fixture", settings=s, download_fn=_explode, local_path=str(supplied))
    assert len(loaded.rows) == 4
    assert loaded.source.startswith("local:")


def test_sha256_mismatch_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register_fixture(monkeypatch)
    s = _settings(tmp_path)
    tampered = _FIXTURE_TAB.replace(b"0.10", b"0.99")  # valid format, wrong bytes

    with pytest.raises(EffDataFetchError) as exc:
        load_dataset("fixture", settings=s, download_fn=_Downloader(tampered))
    assert "sha256" in str(exc.value).lower()
    assert not (tmp_path / "testcommit" / "fixture.tab").exists()  # bad bytes never cached


def test_row_count_mismatch_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register_fixture(monkeypatch, n_rows=99)  # bytes have 4 rows; spec claims 99
    s = _settings(tmp_path)

    with pytest.raises(EffDataFetchError) as exc:
        load_dataset("fixture", settings=s, download_fn=_Downloader(_FIXTURE_TAB))
    assert "row" in str(exc.value).lower()


def test_unknown_dataset_raises(tmp_path: Path) -> None:
    with pytest.raises(EffDataUnknown):
        load_dataset("does-not-exist", settings=_settings(tmp_path))


def test_parse_rejects_bad_header() -> None:
    with pytest.raises(EffDataFetchError) as exc:
        parse_tab(b"col1\tcol2\tcol3\nG\tACGT\t0.1\n")
    assert "header" in str(exc.value).lower()


def test_parse_rejects_nonnumeric_modfreq() -> None:
    with pytest.raises(EffDataFetchError) as exc:
        parse_tab(b"guide\tseq\tmodFreq\nG\tACGTACGTACGTACGTACGTAGG\tnope\n")
    assert "modfreq" in str(exc.value).lower()


# --- numpy correlation (tie-aware; no scipy) ----------------------------------------------------


def test_average_ranks_handles_ties() -> None:
    # 20 and 20 tie for ranks 2 and 3 -> both get 2.5.
    assert average_ranks([10, 20, 20, 40]).tolist() == [1.0, 2.5, 2.5, 4.0]
    # All tied -> all get the mean rank.
    assert average_ranks([5, 5, 5]).tolist() == [2.0, 2.0, 2.0]


def test_spearman_monotonic_extremes() -> None:
    assert spearman_rho([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman_rho([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


def test_pearson_and_spearman_known_value() -> None:
    # Hand-computed: r = 3.0 / 5.0 = 0.6 (and no ties -> spearman == pearson here).
    assert pearson_r([1, 2, 3, 4], [2, 1, 4, 3]) == pytest.approx(0.6)
    assert spearman_rho([1, 2, 3, 4], [2, 1, 4, 3]) == pytest.approx(0.6)


def test_pearson_zero_variance_is_nan() -> None:
    assert math.isnan(pearson_r([1, 1, 1], [1, 2, 3]))


# --- orchestration: honesty labels + structure --------------------------------------------------


def test_run_with_injected_predict_fn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register_fixture(monkeypatch)
    s = _settings(tmp_path)

    # Perfectly monotone scorer (predicted == observed) -> rho == 1.0, exercising the full pipeline.
    def predict_fn(seqs: list[str]) -> list[float]:
        assert all(len(seq) == 23 for seq in seqs)
        return [0.10, 0.50, 0.50, 1.30]

    result = run_on_target_efficiency(
        "fixture", model="deepcrispr", settings=s, download_fn=_Downloader(_FIXTURE_TAB), predict_fn=predict_fn
    )

    assert result.n == 4
    assert result.spearman_rho == pytest.approx(1.0)
    assert result.pearson_r == pytest.approx(1.0)
    # Honesty labels: the fixture dataset has no registered leakage assessment (no primary source),
    # so it must default to 'unknown' with empty evidence -- proving the safe default works.
    assert result.leakage_status == "unknown"
    assert result.leakage_evidence == ""
    assert result.dataset_relationship == "cross_dataset"
    assert "cross-dataset" in result.interpretation.lower()
    assert "leakage status is unknown" in result.interpretation.lower()
    assert result.model_version.endswith(":injected")
    # The (predicted, observed) pairs are preserved per guide -- the calibration inputs.
    assert len(result.pairs) == 4
    assert result.pairs[0].guide == "G1_chr1_100"
    assert [p.observed for p in result.pairs] == [0.10, 0.50, 0.50, 1.30]
    assert result.source.startswith("fetch:")


def test_run_unsupported_model_without_predict_fn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register_fixture(monkeypatch)
    supplied = tmp_path / "f.tab"
    supplied.write_bytes(_FIXTURE_TAB)
    s = _settings(tmp_path, consent=False)

    with pytest.raises(ValueError, match="predict_fn"):
        run_on_target_efficiency("fixture", model="bogus", settings=s, local_path=str(supplied))


def test_every_leakage_claim_is_sourced() -> None:
    """The hard integrity guard (rule 18 / §0): every committed leakage claim cites its primary
    source. 'held_out' and 'contaminated' MUST carry a non-empty evidence string; 'unknown' is the
    only status allowed without one. This makes a 'held_out' label-from-memory structurally
    impossible -- the platform's first rule, enforced at the test boundary.
    """
    for (dataset, model), a in _LEAKAGE.items():
        if a.status in {"held_out", "contaminated"}:
            assert a.evidence, f"({dataset!r}, {model!r}) claims {a.status!r} without primary-source evidence"


def test_chari_deepcrispr_promoted_to_held_out_with_chuai_evidence() -> None:
    """Specific regression: the Chari-2015 vs DeepCRISPR leakage call was promoted from 'unknown'
    to 'held_out' on 2026-06-01 against the Chuai 2018 paper (PMC6020378). If the entry regresses
    or loses its citation, this test fails -- a leakage promotion can never be silent.
    """
    a = _LEAKAGE[("chari2015Train", "deepcrispr")]
    assert a.status == "held_out"
    assert "Chuai" in a.evidence
    assert "PMC6020378" in a.evidence
    assert "Wang 2014" in a.evidence and "Hart 2015" in a.evidence and "Doench 2016" in a.evidence
    assert a.caveat  # residual concern recorded


def test_assess_leakage_unknown_default() -> None:
    """A (dataset, model) pair without an entry must structurally be 'unknown' with empty
    evidence -- the platform never invents a leakage status for an unverified pair."""
    from bioforge.benchmarks.on_target_efficiency import assess_leakage

    a = assess_leakage("nonexistent_dataset", "deepcrispr")
    assert a.status == "unknown"
    assert a.evidence == ""
