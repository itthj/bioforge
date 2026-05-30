"""Tests for find_offtargets — first composite (tool-calling-tool) tool.

Strategy: monkeypatch `_run_ncbi_blast` (same hook the blast tests use) and feed
constructed BLAST records to verify mismatch-counting, risk classification, and the
caveat surface. No network.
"""

from __future__ import annotations

from types import SimpleNamespace

import pydantic
import pytest
from bioforge.tools.sequence import blast as blast_module
from bioforge.tools.sequence.find_offtargets import (
    FindOfftargetsInput,
    find_offtargets,
)


def _fake_hsp(
    *,
    expect=1e-10,
    bits=40.0,
    identities=20,
    align_length=20,
    query: str = "",
    sbjct: str = "",
    match: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        expect=expect,
        bits=bits,
        identities=identities,
        align_length=align_length,
        query_start=1,
        query_end=align_length,
        sbjct_start=1001,
        sbjct_end=1001 + align_length - 1,
        query=query,
        sbjct=sbjct,
        match=match,
    )


def _fake_alignment(
    *,
    accession: str,
    hit_def: str,
    identities: int,
    align_length: int = 20,
    query: str = "",
    sbjct: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        accession=accession,
        hit_def=hit_def,
        hsps=[
            _fake_hsp(
                identities=identities,
                align_length=align_length,
                query=query,
                sbjct=sbjct,
                match="|" * identities + " " * (align_length - identities),
            )
        ],
    )


def _mutate_at(seq: str, positions: list[int], new_base: str = "T") -> str:
    """Return seq with the given 1-based positions replaced by new_base.
    Caller picks new_base to ensure each substitution is actually a mismatch."""
    out = list(seq)
    for p in positions:
        if 1 <= p <= len(out):
            out[p - 1] = new_base if out[p - 1] != new_base else ("A" if new_base != "A" else "C")
    return "".join(out)


def _fake_record(alignments: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(alignments=alignments)


@pytest.fixture
def patch_ncbi(monkeypatch):
    """Patches blast's NCBI hook AND verifies what blast was called with."""
    holder: dict = {"response": None, "calls": []}

    async def _fake_run(*, program, database, sequence, expect, hitlist_size, task=None):
        holder["calls"].append(
            dict(
                program=program,
                database=database,
                sequence=sequence,
                expect=expect,
                hitlist_size=hitlist_size,
                task=task,
            )
        )
        return holder["response"]

    monkeypatch.setattr(blast_module, "_run_ncbi_blast", _fake_run)

    def setter(response):
        holder["response"] = response

    setter.calls = holder["calls"]
    return setter


_GUIDE = "ACGTACGTACGTACGTACGT"  # 20-nt balanced


# --- Mismatch counting ---------------------------------------------------------------


async def test_zero_mismatch_hit_is_high_risk(patch_ncbi) -> None:
    record = _fake_record(
        [
            _fake_alignment(
                accession="NM_007294.4",
                hit_def="Homo sapiens BRCA1 mRNA [Homo sapiens]",
                identities=20,
                align_length=20,
            )
        ]
    )
    patch_ncbi((record, "RID-PERFECT"))

    out = await find_offtargets(FindOfftargetsInput(guide=_GUIDE))
    assert out.num_offtargets_returned == 1
    hit = out.hits[0]
    assert hit.mismatch_count == 0
    assert hit.risk_label == "high"
    assert hit.organism == "Homo sapiens"
    assert hit.query_coverage_percent == 100.0


async def test_2_mismatches_still_high_risk(patch_ncbi) -> None:
    record = _fake_record([_fake_alignment(accession="ACC", hit_def="x", identities=18, align_length=20)])
    patch_ncbi((record, "RID"))
    out = await find_offtargets(FindOfftargetsInput(guide=_GUIDE))
    assert out.hits[0].mismatch_count == 2
    assert out.hits[0].risk_label == "high"


async def test_3_seed_mismatches_lower_risk_via_mit_score(patch_ncbi) -> None:
    """Three mismatches in the seed (positions 14, 16, 18 of a 20-nt guide).
    Hsu weights here are 0.851, 0.828, 0.804 → score = product of (1-w) ≈
    0.149 × 0.172 × 0.196 ≈ 0.005. Falls below the medium threshold (0.1)
    AND triggers the seed-aware medium path due to ≥2 seed mismatches."""
    subject = _mutate_at(_GUIDE, [14, 16, 18])
    record = _fake_record(
        [_fake_alignment(accession="ACC", hit_def="x", identities=17, align_length=20, query=_GUIDE, sbjct=subject)]
    )
    patch_ncbi((record, "RID"))
    out = await find_offtargets(FindOfftargetsInput(guide=_GUIDE))
    hit = out.hits[0]
    assert hit.mismatch_count == 3
    assert hit.used_full_alignment is True
    assert hit.mismatch_positions == [14, 16, 18]
    assert hit.mit_score < 0.05  # all seed mismatches crush the score
    assert hit.risk_label == "low"


async def test_4_distal_mismatches_still_high_risk(patch_ncbi) -> None:
    """4 mismatches but ALL at PAM-distal positions 1-4 (weights 0.0, 0.0,
    0.014, 0.0) → MIT score ≈ 0.986. With distal-only mismatches the new
    classifier flags this as high — which IS the right biology, contradicting
    the old count-only heuristic that called it 'low'."""
    subject = _mutate_at(_GUIDE, [1, 2, 3, 4])
    record = _fake_record(
        [_fake_alignment(accession="ACC", hit_def="x", identities=16, align_length=20, query=_GUIDE, sbjct=subject)]
    )
    patch_ncbi((record, "RID"))
    out = await find_offtargets(FindOfftargetsInput(guide=_GUIDE))
    hit = out.hits[0]
    assert hit.mismatch_count == 4
    assert hit.mit_score > 0.9  # distal mismatches barely hurt
    assert hit.risk_label == "high"


async def test_mismatch_positions_drive_risk_classification(patch_ncbi) -> None:
    """Two hits with the SAME mismatch count but different positions get
    different risk labels — proves the position-aware scoring is doing work."""
    distal_sub = _mutate_at(_GUIDE, [1, 2])  # both PAM-distal, weight 0
    seed_sub = _mutate_at(_GUIDE, [16, 18])  # both seed, weights 0.828, 0.804
    record = _fake_record(
        [
            _fake_alignment(
                accession="DISTAL",
                hit_def="x",
                identities=18,
                align_length=20,
                query=_GUIDE,
                sbjct=distal_sub,
            ),
            _fake_alignment(
                accession="SEED",
                hit_def="x",
                identities=18,
                align_length=20,
                query=_GUIDE,
                sbjct=seed_sub,
            ),
        ]
    )
    patch_ncbi((record, "RID"))
    out = await find_offtargets(FindOfftargetsInput(guide=_GUIDE))
    by_acc = {h.accession: h for h in out.hits}
    assert by_acc["DISTAL"].mit_score > 0.9
    assert by_acc["SEED"].mit_score < 0.05
    assert by_acc["DISTAL"].risk_label == "high"
    assert by_acc["SEED"].risk_label == "low"


async def test_fallback_when_alignment_strings_missing(patch_ncbi) -> None:
    """Pre-existing test fixtures that don't include alignment strings hit the
    fallback path. We surface this per-hit + flag it in the caveats."""
    record = _fake_record(
        [_fake_alignment(accession="OLD", hit_def="x", identities=17, align_length=20)]
        # No query=/sbjct= → empty alignment strings → fallback.
    )
    patch_ncbi((record, "RID"))
    out = await find_offtargets(FindOfftargetsInput(guide=_GUIDE))
    hit = out.hits[0]
    assert hit.used_full_alignment is False
    # Fallback caveat surfaces.
    joined = " ".join(out.caveats).lower()
    assert "fallback" in joined or "older blast" in joined or "under-state" in joined


async def test_above_max_mismatches_excluded(patch_ncbi) -> None:
    record = _fake_record([_fake_alignment(accession="ACC", hit_def="x", identities=10, align_length=20)])
    patch_ncbi((record, "RID"))
    out = await find_offtargets(FindOfftargetsInput(guide=_GUIDE, max_mismatches=4))
    assert out.num_offtargets_returned == 0


async def test_partial_alignment_low_coverage_is_low_risk(patch_ncbi) -> None:
    # 15/15 perfect identity but only 15 of 20 nt aligned (75% coverage)
    record = _fake_record([_fake_alignment(accession="ACC", hit_def="x", identities=15, align_length=15)])
    patch_ncbi((record, "RID"))
    out = await find_offtargets(FindOfftargetsInput(guide=_GUIDE))
    hit = out.hits[0]
    assert hit.query_coverage_percent < 80
    assert hit.risk_label == "low"
    assert "partial match" in hit.risk_reason.lower()


# --- Composition: blast was called with the right parameters -------------------------


async def test_uses_blastn_short_task(patch_ncbi) -> None:
    patch_ncbi((_fake_record([]), "RID-EMPTY"))
    await find_offtargets(FindOfftargetsInput(guide=_GUIDE))
    call = patch_ncbi.calls[0]
    assert call["task"] == "blastn-short"
    assert call["program"] == "blastn"
    assert call["sequence"] == _GUIDE


async def test_propagates_database_parameter(patch_ncbi) -> None:
    patch_ncbi((_fake_record([]), "RID"))
    await find_offtargets(FindOfftargetsInput(guide=_GUIDE, database="refseq_genomic"))
    assert patch_ncbi.calls[0]["database"] == "refseq_genomic"


async def test_propagates_high_expect_threshold_for_short_queries(patch_ncbi) -> None:
    """find_offtargets defaults to E=1000 for short queries; that should flow through."""
    patch_ncbi((_fake_record([]), "RID"))
    await find_offtargets(FindOfftargetsInput(guide=_GUIDE))
    assert patch_ncbi.calls[0]["expect"] >= 100.0


# --- Sorting + capping --------------------------------------------------------------


async def test_hits_sorted_by_mit_score_descending(patch_ncbi) -> None:
    """Three hits with distinguishing mismatch positions get sorted by MIT
    score: perfect match → seed mismatch → all-seed → progressively lower."""
    record = _fake_record(
        [
            _fake_alignment(  # all-seed: lowest score
                accession="LOW",
                hit_def="x",
                identities=17,
                align_length=20,
                query=_GUIDE,
                sbjct=_mutate_at(_GUIDE, [14, 16, 18]),
            ),
            _fake_alignment(  # perfect match: highest score
                accession="HIGH",
                hit_def="x",
                identities=20,
                align_length=20,
                query=_GUIDE,
                sbjct=_GUIDE,
            ),
            _fake_alignment(  # one seed mismatch: middle score (1 - 0.851 = 0.149)
                accession="MED",
                hit_def="x",
                identities=19,
                align_length=20,
                query=_GUIDE,
                sbjct=_mutate_at(_GUIDE, [14]),
            ),
        ]
    )
    patch_ncbi((record, "RID"))
    out = await find_offtargets(FindOfftargetsInput(guide=_GUIDE))
    accessions = [h.accession for h in out.hits]
    assert accessions == ["HIGH", "MED", "LOW"]
    # MIT scores strictly decreasing.
    assert out.hits[0].mit_score > out.hits[1].mit_score > out.hits[2].mit_score


async def test_max_hits_caps_response(patch_ncbi) -> None:
    record = _fake_record(
        [_fake_alignment(accession=f"ACC{i}", hit_def="x", identities=20, align_length=20) for i in range(10)]
    )
    patch_ncbi((record, "RID"))
    out = await find_offtargets(FindOfftargetsInput(guide=_GUIDE, max_hits=3))
    assert out.num_offtargets_returned == 3


# --- Honesty -------------------------------------------------------------------------


async def test_caveats_mention_missing_pam_verification(patch_ncbi) -> None:
    patch_ncbi((_fake_record([]), "RID"))
    out = await find_offtargets(FindOfftargetsInput(guide=_GUIDE))
    text = " ".join(out.caveats).lower()
    assert "pam" in text and "not verified" in text
    # MIT / Hsu 2013 scoring is the new specificity metric — it implicitly weights
    # seed-region mismatches, but mention CFD as the next-step refinement.
    assert "mit" in text or "hsu" in text or "cfd" in text
    assert "bulge" in text or "indel" in text  # mentions bulge/indel limitation


# --- PAM verification (full CFD) -----------------------------------------------------


async def test_verify_pam_computes_full_cfd(patch_ncbi, monkeypatch) -> None:
    from bioforge.tools.sequence import find_offtargets as fo
    from bioforge.tools.sequence.offtarget_scoring import cfd_score

    sbjct = _mutate_at(_GUIDE, [3, 15])  # 2-mismatch off-target -> full-alignment path -> CFD applies
    record = _fake_record(
        [
            _fake_alignment(
                accession="NC_000001.11", hit_def="chr1 [Homo sapiens]", identities=18, query=_GUIDE, sbjct=sbjct
            )
        ]
    )
    patch_ncbi((record, "RID"))

    # sbjct coords default to 1001..1020 (plus). PAM "AGG" at 1021..1023; window starts at 995.
    window = "T" * 6 + sbjct + "AGG" + "C" * 3  # plus-strand window for coords 995..1026
    captured: dict = {}

    def fake_efetch(*, accession, seq_start, seq_stop, email):
        captured["args"] = (accession, seq_start, seq_stop)
        return window

    monkeypatch.setattr(fo, "efetch_flank", fake_efetch)

    out = await find_offtargets(FindOfftargetsInput(guide=_GUIDE, verify_pam=True))
    hit = out.hits[0]
    assert hit.pam == "AGG"
    assert hit.cfd_full_score == round(cfd_score(_GUIDE, sbjct, "GG"), 4)
    assert hit.cfd_mismatch_score is not None
    assert captured["args"] == ("NC_000001.11", 995, 1026)  # flank fetched with the right coords
    assert any("pam verification was on" in c.lower() for c in out.caveats)


async def test_verify_pam_soundness_failure_falls_back(patch_ncbi, monkeypatch) -> None:
    # efetch returns a locus whose bases disagree with the BLAST subject -> the reconstruction
    # fails the soundness gate -> no PAM, no full CFD, but the mismatch component is kept.
    from bioforge.tools.sequence import find_offtargets as fo

    sbjct = _mutate_at(_GUIDE, [3, 15])  # 2-mismatch off-target -> CFD-scorable (full-alignment path)
    record = _fake_record([_fake_alignment(accession="ACC", hit_def="x", identities=18, query=_GUIDE, sbjct=sbjct)])
    patch_ncbi((record, "RID"))
    wrong_window = "T" * 6 + "A" * 20 + "AGG" + "C" * 3  # locus is poly-A, disagrees with sbjct
    monkeypatch.setattr(fo, "efetch_flank", lambda **_kw: wrong_window)

    out = await find_offtargets(FindOfftargetsInput(guide=_GUIDE, verify_pam=True))
    hit = out.hits[0]
    assert hit.pam is None
    assert hit.cfd_full_score is None
    assert hit.cfd_mismatch_score is not None  # mismatch component (upper bound) still reported


async def test_verify_pam_off_by_default_does_not_fetch(patch_ncbi, monkeypatch) -> None:
    from bioforge.tools.sequence import find_offtargets as fo

    sbjct = _mutate_at(_GUIDE, [3, 15])  # 2-mismatch off-target -> CFD-scorable (full-alignment path)
    record = _fake_record([_fake_alignment(accession="ACC", hit_def="x", identities=18, query=_GUIDE, sbjct=sbjct)])
    patch_ncbi((record, "RID"))

    def boom(**_kw):
        raise AssertionError("efetch must not be called when verify_pam is off")

    monkeypatch.setattr(fo, "efetch_flank", boom)

    out = await find_offtargets(FindOfftargetsInput(guide=_GUIDE))  # verify_pam defaults to False
    hit = out.hits[0]
    assert hit.pam is None
    assert hit.cfd_full_score is None
    assert hit.cfd_mismatch_score is not None


# --- Adversarial validation ----------------------------------------------------------


async def test_rejects_short_guide() -> None:
    with pytest.raises(pydantic.ValidationError):
        FindOfftargetsInput(guide="ACGT")


async def test_rejects_non_dna() -> None:
    with pytest.raises(pydantic.ValidationError, match="non-DNA"):
        FindOfftargetsInput(guide="A" * 19 + "Z")


async def test_is_registered_as_expensive(patch_ncbi) -> None:
    """find_offtargets transitively calls blast → must be marked expensive so the
    approval gate fires."""
    from bioforge.tools.registry import get_tool

    spec = get_tool("find_offtargets")
    assert spec.cost_hint == "expensive"
    assert "crispr" in spec.tags
    assert "offtarget" in spec.tags


# --- Composition with design_guides → find_offtargets workflow -----------------------


async def test_full_design_then_offtarget_pipeline(patch_ncbi) -> None:
    """Pick a guide via design_guides, then feed it to find_offtargets. Should work
    without any glue code — proves the composition pattern."""
    from bioforge.tools.sequence.design_guides import (
        DesignGuidesInput,
        design_guides,
    )

    # Construct a target with a known guide site
    target = "A" * 10 + _GUIDE + "AGG" + "T" * 17
    design_out = await design_guides(DesignGuidesInput(sequence=target, strands=["+"]))
    assert design_out.num_returned >= 1
    guide = design_out.guides[0].protospacer

    # Now feed it to find_offtargets
    record = _fake_record(
        [
            _fake_alignment(
                accession="EXPECTED_HIT",
                hit_def="Some homolog [Homo sapiens]",
                identities=20,
                align_length=20,
            )
        ]
    )
    patch_ncbi((record, "RID-PIPELINE"))

    off_out = await find_offtargets(FindOfftargetsInput(guide=guide))
    assert off_out.num_offtargets_returned == 1
    assert off_out.blast_request_id == "RID-PIPELINE"
