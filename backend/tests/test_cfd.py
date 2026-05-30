"""CFD off-target score (Doench 2016) — sourced data + engine parity (Phase 2, rule 16).

Verifies the committed CFD tables match the published shape and that our engine reproduces
the canonical CRISPOR/Doench `calc_cfd` formula EXACTLY — an independent reference impl over
the SAME sourced data (`data/cfd_doench2016.json`). The values are sourced verbatim, never
transcribed from memory.
"""

from __future__ import annotations

import pytest
from bioforge.tools.sequence.offtarget_scoring import (
    _cfd_tables,
    cfd_mismatch_component,
    cfd_score,
)

_GUIDE = "GAGTCCGAGCAGAAGAAGAA"  # 20-mer, EMX1-style


def _reference_cfd(wt: str, sg: str, pam: str) -> float:
    """Verbatim Doench/CRISPOR calc_cfd over the committed tables — independent of our engine."""
    mm, pam_scores = _cfd_tables()
    comp = {"A": "T", "C": "G", "G": "C", "T": "A", "U": "A"}
    wt = wt.replace("T", "U")
    sg = sg.replace("T", "U")
    score = 1.0
    for i, sl in enumerate(sg):
        if wt[i] != sl:
            score *= mm["r" + wt[i] + ":d" + comp[sl] + "," + str(i + 1)]
    return score * pam_scores[pam]


def test_data_file_shape_and_ranges() -> None:
    mm, pam = _cfd_tables()
    assert len(mm) == 240  # 20 positions x 12 mismatch types
    assert len(pam) == 16  # NN PAM table
    assert all(0.0 <= v <= 1.0 for v in mm.values())
    assert all(0.0 <= v <= 1.0 for v in pam.values())
    assert pam["GG"] == pytest.approx(1.0)


def test_perfect_match_equals_pam_weight() -> None:
    _, pam = _cfd_tables()
    assert cfd_score(_GUIDE, _GUIDE, "GG") == pytest.approx(1.0)
    assert cfd_score(_GUIDE, _GUIDE, "AG") == pytest.approx(pam["AG"])


def test_engine_matches_canonical_reference() -> None:
    cases = [
        (_GUIDE, _GUIDE, "GG"),
        (_GUIDE, "A" + _GUIDE[1:], "GG"),  # mismatch at position 1
        (_GUIDE, _GUIDE[:-1] + ("T" if _GUIDE[-1] != "T" else "C"), "AG"),  # pos 20 + different PAM
        (_GUIDE, "GTGTACGAGCAGAAGTAGAA", "GG"),  # several mismatches
        ("ACGTACGTACGTACGTACGT", "TGCATGCATGCATGCATGCA", "TG"),  # all positions mismatch
    ]
    for wt, sg, pam in cases:
        assert cfd_score(wt, sg, pam) == pytest.approx(_reference_cfd(wt, sg, pam))


def test_full_cfd_is_mismatch_component_times_pam() -> None:
    _, pam = _cfd_tables()
    off = "GAGTCCGAGCAGAAGAATAA"  # one mismatch vs _GUIDE near the 3' end
    assert cfd_score(_GUIDE, off, "GG") == pytest.approx(cfd_mismatch_component(_GUIDE, off) * pam["GG"])
    assert cfd_mismatch_component(_GUIDE, off) < 1.0  # a mismatch must penalize


def test_invalid_inputs_raise() -> None:
    with pytest.raises(ValueError):
        cfd_score("ACGT", "ACG", "GG")  # length mismatch
    with pytest.raises(ValueError):
        cfd_score(_GUIDE, _GUIDE, "ZZ")  # PAM not in table
    with pytest.raises(ValueError):
        cfd_mismatch_component(_GUIDE, "N" + _GUIDE[1:])  # non-ACGT base at a mismatch
