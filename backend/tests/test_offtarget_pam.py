"""Off-target PAM extraction — pure coordinate/strand logic + the soundness gate.

These tests carry the §0 weight for the CFD-with-PAM path: they prove the plus- AND minus-strand
arithmetic against worked coordinate examples, and that a wrong/mismatched window makes the
soundness gate REFUSE a PAM (return None) rather than emit a confidently-wrong one. No network —
`efetch_flank` is exercised with an injected fake.
"""

from __future__ import annotations

import pytest
from bioforge.tools.sequence.offtarget_pam import (
    OfftargetPamError,
    PamExtraction,
    _revcomp,
    efetch_flank,
    extract_pam,
)

_OT = "GAGTCCGAGCAGAAGAAGAA"  # a 20-nt off-target protospacer (EMX1-like)


def test_revcomp() -> None:
    assert _revcomp("GAGTCCGAGCAGAAGAAGAA") == "TTCTTCTTCTGCTCGGACTC"
    assert _revcomp(_revcomp(_OT)) == _OT


# --- plus-strand hit ----------------------------------------------------------------
#
# Protospacer at plus coords [101,120]; PAM "AGG" at [121,123]; window starts at coord 95.


def test_extract_pam_plus_strand() -> None:
    window = "TTTTTT" + _OT + "AGG" + "CCCCCCC"  # coords 95..130
    res = extract_pam(
        window_plus=window,
        window_start=95,
        subject_start=101,  # ascending -> plus strand
        subject_end=120,
        guide_len=20,
        expected_protospacer=_OT,
    )
    assert isinstance(res, PamExtraction)
    assert res.strand == "plus"
    assert res.protospacer == _OT
    assert res.pam3 == "AGG"
    assert res.pam2 == "GG"  # the GG of NGG that cfd_score consumes


# --- minus-strand hit ---------------------------------------------------------------
#
# Off-target on the minus strand. The PLUS strand at the locus is revcomp(_OT); the PAM on the
# minus strand (3' of the protospacer) sits at lower plus coords, so plus[98..100] = revcomp("AGG")
# = "CCT". subject_start > subject_end signals the minus strand.


def test_extract_pam_minus_strand() -> None:
    window = "GGG" + "CCT" + _revcomp(_OT) + "AAAAA"  # coords 95..125
    res = extract_pam(
        window_plus=window,
        window_start=95,
        subject_start=120,  # descending -> minus strand
        subject_end=101,
        guide_len=20,
        expected_protospacer=_OT,
    )
    assert isinstance(res, PamExtraction)
    assert res.strand == "minus"
    assert res.protospacer == _OT  # reconstructed 5'->3' on the matching strand
    assert res.pam3 == "AGG"
    assert res.pam2 == "GG"


# --- the soundness gate (the §0 safety net) -----------------------------------------


def test_soundness_gate_rejects_mismatched_protospacer() -> None:
    # The window says the locus is _OT, but BLAST claims a different off-target here. The
    # reconstruction won't match -> refuse the PAM (return None), never emit a wrong one.
    window = "TTTTTT" + _OT + "AGG" + "CCCCCCC"
    res = extract_pam(
        window_plus=window,
        window_start=95,
        subject_start=101,
        subject_end=120,
        guide_len=20,
        expected_protospacer="AAAAAAAAAAAAAAAAAAAA",  # disagrees with the window
    )
    assert res is None


def test_pam_outside_window_returns_none() -> None:
    # Window ends right at the protospacer 3' end — no room for the plus-strand PAM.
    window = "TTTTTT" + _OT  # coords 95..120, nothing at 121+
    res = extract_pam(
        window_plus=window,
        window_start=95,
        subject_start=101,
        subject_end=120,
        guide_len=20,
        expected_protospacer=_OT,
    )
    assert res is None


def test_non_acgt_pam_returns_none() -> None:
    window = "TTTTTT" + _OT + "ANG" + "CCCCCCC"  # PAM region has an N
    res = extract_pam(
        window_plus=window,
        window_start=95,
        subject_start=101,
        subject_end=120,
        guide_len=20,
        expected_protospacer=_OT,
    )
    assert res is None


def test_span_not_guide_length_returns_none() -> None:
    window = "TTTTTT" + _OT + "AGG" + "CCCCCCC"
    res = extract_pam(
        window_plus=window,
        window_start=95,
        subject_start=101,
        subject_end=119,  # 19-nt span, not 20 -> gapped/partial, refuse
        guide_len=20,
        expected_protospacer=_OT,
    )
    assert res is None


# --- efetch_flank (network injected) ------------------------------------------------


def test_efetch_flank_strips_fasta_header() -> None:
    def fake(accession: str, lo: int, hi: int, email: str) -> str:
        assert accession == "NC_000001.11"
        assert (lo, hi) == (95, 130)
        return ">NC_000001.11:95-130 Homo sapiens\nTTTTTTGAGTCCGAGCAGAAGAAGAA\nAGGCCCCCCC\n"

    seq = efetch_flank(accession="NC_000001.11", seq_start=95, seq_stop=130, email="x@y.z", efetch_fn=fake)
    assert seq == "TTTTTTGAGTCCGAGCAGAAGAAGAAAGGCCCCCCC"


def test_efetch_flank_normalizes_coord_order() -> None:
    seen: dict[str, int] = {}

    def fake(accession: str, lo: int, hi: int, email: str) -> str:
        seen["lo"], seen["hi"] = lo, hi
        return ">h\nACGT\n"

    efetch_flank(accession="A", seq_start=130, seq_stop=95, email="", efetch_fn=fake)
    assert (seen["lo"], seen["hi"]) == (95, 130)  # normalized to lo<=hi for NCBI


def test_efetch_flank_wraps_failures() -> None:
    def boom(accession: str, lo: int, hi: int, email: str) -> str:
        raise RuntimeError("network down")

    with pytest.raises(OfftargetPamError):
        efetch_flank(accession="A", seq_start=1, seq_stop=10, email="", efetch_fn=boom)


def test_efetch_flank_empty_result_raises() -> None:
    with pytest.raises(OfftargetPamError):
        efetch_flank(accession="A", seq_start=1, seq_stop=10, email="", efetch_fn=lambda *_: ">header only\n")
