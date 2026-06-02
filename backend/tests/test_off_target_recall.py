"""§13 off-target recall benchmark -- unit tests. Hermetic: injected fixture data + score fn.

The real annotOfftargets x CFD run is exercised end-to-end by the integration check, but the
math + honesty rails are validated here without network or the real CFD weights.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from bioforge.benchmarks import effdata
from bioforge.benchmarks.effdata import EffDataset, parse_offtarget_tab
from bioforge.benchmarks.off_target_recall import (
    _LEAKAGE,
    _peel_protospacer_and_pam,
    _recall_at_quantile,
    assess_leakage_offtarget,
    run_off_target_recall,
)
from bioforge.config import settings as _settings_singleton

# 4 (guide, off-target) pairs across two sgRNAs. Each guide_seq / ot_seq is a valid 23-mer NGG.
# readFraction is the ground-truth target; mismatches counted for one of them.
_FIXTURE_OT = (
    b"name\tguideSeq\totSeq\treadFraction\tmismatches\n"
    b"Tsai_EMX1\tGAGTCCGAGCAGAAGAAGAAGGG\tGAGTCCGAGCAGAAGAAGAAGGG\t1.000\t0\n"
    b"Tsai_EMX1\tGAGTCCGAGCAGAAGAAGAAGGG\tGAGTCCAAGCAGAAGAAGAAGGG\t0.500\t1\n"
    b"Frock_VEGFA\tGACCCCCTCCACCCCGCCTCAGG\tGACCCCCTCCACCCCGCCTCAGG\t0.800\t0\n"
    b"Frock_VEGFA\tGACCCCCTCCACCCCGCCTCAGG\tGACCCCCACCCACCCCGCCTCAG\t0.200\t3\n"
)


def _register_fixture(monkeypatch: pytest.MonkeyPatch, *, n_rows: int = 4) -> EffDataset:
    spec = EffDataset(
        name="fixture_ot",
        upstream_relpath="out/fixture_ot.tsv",
        expected_sha256=hashlib.sha256(_FIXTURE_OT).hexdigest(),
        n_rows=n_rows,
        citation="fixture off-target citation",
        notes="fixture",
        kind="off_target",
    )
    monkeypatch.setitem(effdata.DATASETS, "fixture_ot", spec)
    return spec


def _settings(tmp_path: Path):
    return _settings_singleton.model_copy(
        update={
            "crispor_effdata_dir": str(tmp_path),
            "crispor_effdata_consent": False,
            "crispor_effdata_commit": "testcommit",
        }
    )


# --- parser ---------------------------------------------------------------------------------------


def test_parse_offtarget_tab_happy_path() -> None:
    rows = parse_offtarget_tab(_FIXTURE_OT)
    assert len(rows) == 4
    assert rows[0].guide_name == "Tsai_EMX1"
    assert rows[0].guide_seq == "GAGTCCGAGCAGAAGAAGAAGGG"
    assert rows[0].read_fraction == 1.0
    assert rows[3].mismatches == 3


def test_parse_offtarget_tab_rejects_missing_columns() -> None:
    bad = b"name\tguideSeq\totSeq\nx\tGAGTCCGAGCAGAAGAAGAAGGG\tGAGTCCGAGCAGAAGAAGAAGGG\n"
    with pytest.raises(Exception, match="missing required columns"):
        parse_offtarget_tab(bad)


# --- helpers --------------------------------------------------------------------------------------


def test_peel_protospacer_and_pam() -> None:
    g = _peel_protospacer_and_pam("GAGTCCGAGCAGAAGAAGAAGGG")
    assert g == ("GAGTCCGAGCAGAAGAAGAA", "GG")
    # Wrong length / non-ACGT / etc. -> None (skipped, never silently scored)
    assert _peel_protospacer_and_pam("TOOSHORT") is None
    assert _peel_protospacer_and_pam("NNNGTCCGAGCAGAAGAAGAAGGG"[:23]) is None  # contains N


def test_recall_at_quantile_perfect_and_inverse() -> None:
    preds = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    obs = preds[:]  # perfect ranker
    assert _recall_at_quantile(preds, obs, 0.5) == pytest.approx(1.0)
    inverse = list(reversed(preds))  # anti-monotone
    assert _recall_at_quantile(preds, inverse, 0.3) == pytest.approx(0.0)


# --- orchestration: honesty labels + math ---------------------------------------------------------


def test_run_with_injected_perfect_scorer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register_fixture(monkeypatch)
    s = _settings(tmp_path)
    supplied = tmp_path / "f.tsv"
    supplied.write_bytes(_FIXTURE_OT)

    # Score returns the read_fraction encoded into the guide name -- a perfect ranker.
    rf_by_pair = {
        ("GAGTCCGAGCAGAAGAAGAA", "GAGTCCGAGCAGAAGAAGAA"): 1.0,
        ("GAGTCCGAGCAGAAGAAGAA", "GAGTCCAAGCAGAAGAAGAA"): 0.5,
        ("GACCCCCTCCACCCCGCCTC", "GACCCCCTCCACCCCGCCTC"): 0.8,
        ("GACCCCCTCCACCCCGCCTC", "GACCCCCACCCACCCCGCCT"): 0.2,
    }

    def perfect(guide_proto: str, ot_proto: str, _pam: str) -> float:
        return rf_by_pair[(guide_proto, ot_proto)]

    result = run_off_target_recall("fixture_ot", settings=s, local_path=str(supplied), score_fn=perfect)

    assert result.n == 4
    assert result.n_skipped == 0
    assert result.spearman_rho == pytest.approx(1.0)
    # Honesty: unknown leakage (fixture has no entry) -- safe default works.
    assert result.leakage_status == "unknown"
    assert result.leakage_evidence == ""
    assert "leakage status is unknown" in result.interpretation.lower()
    assert "discrimination" in result.interpretation.lower()
    # Per-pair (cfd, readFraction) preserved -- the reliability-diagram inputs.
    assert len(result.pairs) == 4
    assert result.pairs[0].guide_name == "Tsai_EMX1"


def test_run_skips_malformed_rows_rather_than_scoring_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Mix one malformed row (an N in the off-target seq -- correct length, invalid base) with valids.
    bad_blob = (
        b"name\tguideSeq\totSeq\treadFraction\tmismatches\n"
        b"good\tGAGTCCGAGCAGAAGAAGAAGGG\tGAGTCCGAGCAGAAGAAGAAGGG\t1.000\t0\n"
        b"good2\tGAGTCCGAGCAGAAGAAGAAGGG\tGAGTCCAAGCAGAAGAAGAAGGG\t0.500\t1\n"
        b"bad\tGAGTCCGAGCAGAAGAAGAAGGG\tNGGTCCGAGCAGAAGAAGAAGGG\t0.300\t1\n"
    )
    spec = EffDataset(
        name="fixture_skip",
        upstream_relpath="out/fixture_skip.tsv",
        expected_sha256=hashlib.sha256(bad_blob).hexdigest(),
        n_rows=3,
        citation="x",
        notes="x",
        kind="off_target",
    )
    monkeypatch.setitem(effdata.DATASETS, "fixture_skip", spec)
    supplied = tmp_path / "skip.tsv"
    supplied.write_bytes(bad_blob)

    def constant(_g: str, _o: str, _p: str) -> float:
        return 0.5

    result = run_off_target_recall(
        "fixture_skip", settings=_settings(tmp_path), local_path=str(supplied), score_fn=constant
    )
    assert result.n == 2
    assert result.n_skipped == 1


def test_run_rejects_on_target_dataset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Register a kind='on_target' fixture; run_off_target_recall must refuse it -- a wrong-kind
    # dataset must surface as a typed error, never silently scored as off-target.
    ot_blob = b"guide\tseq\tmodFreq\nG1\tGAGTCCGAGCAGAAGAAGAAGGG\t0.5\nG2\tGAGTCCAAGCAGAAGAAGAAGGG\t0.7\n"
    spec = EffDataset(
        name="fixture_on",
        upstream_relpath="effData/fixture_on.tab",
        expected_sha256=hashlib.sha256(ot_blob).hexdigest(),
        n_rows=2,
        citation="x",
        notes="x",
        kind="on_target",
    )
    monkeypatch.setitem(effdata.DATASETS, "fixture_on", spec)
    supplied = tmp_path / "on.tab"
    supplied.write_bytes(ot_blob)

    with pytest.raises(ValueError, match="off_target"):
        run_off_target_recall("fixture_on", settings=_settings(tmp_path), local_path=str(supplied))


def test_leakage_registry_unknown_with_caveat() -> None:
    a = _LEAKAGE[("annotOfftargets", "cfd_full")]
    assert a.status == "unknown"  # not promoted from memory -- waits for Doench-2016 verification
    assert a.evidence == ""
    assert "Doench 2016" in a.caveat  # the residual concern IS recorded, just not as evidence


def test_assess_leakage_offtarget_unknown_default() -> None:
    a = assess_leakage_offtarget("nope", "cfd_full")
    assert a.status == "unknown"
    assert a.evidence == ""
