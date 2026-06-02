"""section-13 edit-outcome runner -- unit tests (no network, no Docker).

The FORECasT predictor is mocked via an injected `run_fn`, observed + target fixtures are
monkeypatched + supplied by `local_path`, and the per-guide TVD/JSD + aggregation are checked
against hand-computed values. The REAL K562 x FORECasT run is a `-m docker -m online` e2e.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from bioforge.benchmarks import forecast_profiles
from bioforge.benchmarks.edit_outcome_published_run import run_edit_outcome_agreement
from bioforge.benchmarks.forecast_profiles import ObservedSpec, TargetLibrarySpec
from bioforge.config import settings as _settings_singleton
from bioforge.tools.sequence.models.forecast import ForecastUnavailable

# Real designed targets (Allen 2018 example) for two oligos; pam_index 56 is valid in each 78-mer.
_T1 = "TAAACTCCAACTACTCTACTAGTCCTGTACTTTCGCAATTTAAGCTGAAGCTACATGGGTTTAAGGGCAGTCACATGTG"
_T2 = "ATGGCCGAAATGTAAATAGACTATGGGAGTGCGCGTTAGGTCGTGTTAGTAGTACCAGGCGCTAACGCTAAGTTAGGAT"

# Observed: Oligo1062 = {D1:0.75, I1:0.25} over 8 reads; Oligo46510 = {D1:1.0} over 4 reads.
_OBS_TEXT = "@@@Oligo1062\nD1_L-2R0\t6\tACGT\nI1_L-1C1R0\t2\tACGT\n-\t1000\tACGT\n@@@Oligo46510\nD1_L-2R0\t4\tACGT\n"


def _make_zip(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in members.items():
            zf.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), text)
    return buf.getvalue()


_OBS_ZIP = _make_zip({"S/Oligos_1/a_processedindels.txt": _OBS_TEXT})
_TARGET_FASTA = (
    f">Oligo1062_GATTTAAGCTGAAGCTACAT 56 FORWARD\n{_T1}\n>Oligo46510_GAGGTCGTGTTAGTAGTACC 56 FORWARD\n{_T2}\n"
).encode()


def _register(monkeypatch: pytest.MonkeyPatch, *, target_oligos: int = 2, target_fasta: bytes = _TARGET_FASTA):
    monkeypatch.setitem(
        forecast_profiles.OBSERVED,
        "obs_fix",
        ObservedSpec(
            name="obs_fix",
            download_url="https://example.invalid/o",
            cache_filename="o.zip",
            expected_sha256=hashlib.sha256(_OBS_ZIP).hexdigest(),
            n_oligos=2,
            sample_label="K562 (fixture)",
            citation="observed citation",
            notes="fixture",
        ),
    )
    monkeypatch.setitem(
        forecast_profiles.TARGET_LIBRARY,
        "lib_fix",
        TargetLibrarySpec(
            name="lib_fix",
            download_url="https://example.invalid/l",
            cache_filename="l.txt",
            expected_sha256=hashlib.sha256(target_fasta).hexdigest(),
            n_oligos=target_oligos,
            citation="library citation",
            notes="fixture",
        ),
    )


def _settings(tmp_path: Path):
    return _settings_singleton.model_copy(
        update={
            "forecast_profiles_dir": str(tmp_path),
            "forecast_enabled": True,
            "forecast_runner": "local",
            "forecast_python": "python",
        }
    )


def _write(tmp_path: Path, obs: bytes = _OBS_ZIP, lib: bytes = _TARGET_FASTA) -> tuple[str, str]:
    op = tmp_path / "obs.zip"
    op.write_bytes(obs)
    lp = tmp_path / "lib.txt"
    lp.write_bytes(lib)
    return str(op), str(lp)


def _predicted_for(seq: str) -> dict[str, float]:
    # Both targets predict {D1_L-2R0: 1.0}; vs observed this gives a known TVD per oligo.
    return {"D1_L-2R0": 1.0}


def _mock_run_fn(argv: list[str], stdin_text: str, timeout: float) -> str:
    reqs = json.loads(stdin_text)["requests"]
    return json.dumps({"results": [{"predictions": _predicted_for(r["sequence"])} for r in reqs]})


def test_run_scores_and_aggregates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register(monkeypatch)
    obs_p, lib_p = _write(tmp_path)
    res = run_edit_outcome_agreement(
        "obs_fix",
        "lib_fix",
        settings=_settings(tmp_path),
        min_reads=1,
        observed_local_path=obs_p,
        target_local_path=lib_p,
        run_fn=_mock_run_fn,
    )
    assert res.n_guides == 2
    assert res.n_skipped == 0
    assert res.join_coverage == pytest.approx(1.0)
    by_id = {g.oligo_id: g for g in res.per_guide}
    # Oligo1062: predicted {D1:1.0} vs observed {D1:0.75, I1:0.25} -> TVD = 0.5*(0.25+0.25) = 0.25
    assert by_id["Oligo1062"].tvd == pytest.approx(0.25)
    # Oligo46510: predicted {D1:1.0} vs observed {D1:1.0} -> TVD = 0
    assert by_id["Oligo46510"].tvd == pytest.approx(0.0)
    assert res.tvd_median == pytest.approx(0.125)
    assert res.leakage_status == "unknown"
    assert "in-distribution" in res.leakage_caveat.lower()
    assert res.model_version == "allen-2018"
    assert res.observed_sha256 and res.target_sha256


def test_min_reads_filters_low_coverage_oligos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register(monkeypatch)
    obs_p, lib_p = _write(tmp_path)
    # min_reads=5 -> Oligo46510 (4 reads) is excluded; only Oligo1062 (8 reads) eligible.
    res = run_edit_outcome_agreement(
        "obs_fix",
        "lib_fix",
        settings=_settings(tmp_path),
        min_reads=5,
        observed_local_path=obs_p,
        target_local_path=lib_p,
        run_fn=_mock_run_fn,
    )
    assert res.n_eligible == 1
    assert res.n_guides == 1
    assert res.per_guide[0].oligo_id == "Oligo1062"


def test_low_join_coverage_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Target library has only ONE of the two eligible observed oligos -> coverage 0.5 < 0.8.
    lib = f">Oligo1062_GATTTAAGCTGAAGCTACAT 56 FORWARD\n{_T1}\n".encode()
    _register(monkeypatch, target_oligos=1, target_fasta=lib)
    obs_p, lib_p = _write(tmp_path, lib=lib)
    with pytest.raises(ValueError, match="coverage"):
        run_edit_outcome_agreement(
            "obs_fix",
            "lib_fix",
            settings=_settings(tmp_path),
            min_reads=1,
            observed_local_path=obs_p,
            target_local_path=lib_p,
            run_fn=_mock_run_fn,
        )


def test_all_predictions_fail_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register(monkeypatch)
    obs_p, lib_p = _write(tmp_path)

    def empty_run_fn(argv: list[str], stdin_text: str, timeout: float) -> str:
        reqs = json.loads(stdin_text)["requests"]
        return json.dumps({"results": [{"predictions": {}} for _ in reqs]})  # all empty -> all skipped

    with pytest.raises(ForecastUnavailable, match="zero"):
        run_edit_outcome_agreement(
            "obs_fix",
            "lib_fix",
            settings=_settings(tmp_path),
            min_reads=1,
            observed_local_path=obs_p,
            target_local_path=lib_p,
            run_fn=empty_run_fn,
        )
