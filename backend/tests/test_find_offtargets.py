"""Tests for find_offtargets — first composite (tool-calling-tool) tool.

Strategy: monkeypatch `_run_ncbi_blast` (same hook the blast tests use) and feed
constructed BLAST records to verify mismatch-counting, risk classification, and the
caveat surface. No network.
"""

from __future__ import annotations

from types import SimpleNamespace

import pydantic
import pytest

from bioforge.tools.base import ToolError
from bioforge.tools.sequence import blast as blast_module
from bioforge.tools.sequence.find_offtargets import (
    FindOfftargetsInput,
    find_offtargets,
)


def _fake_hsp(*, expect=1e-10, bits=40.0, identities=20, align_length=20) -> SimpleNamespace:
    return SimpleNamespace(
        expect=expect,
        bits=bits,
        identities=identities,
        align_length=align_length,
        query_start=1,
        query_end=align_length,
        sbjct_start=1001,
        sbjct_end=1001 + align_length - 1,
    )


def _fake_alignment(
    *, accession: str, hit_def: str, identities: int, align_length: int = 20
) -> SimpleNamespace:
    return SimpleNamespace(
        accession=accession,
        hit_def=hit_def,
        hsps=[_fake_hsp(identities=identities, align_length=align_length)],
    )


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
    record = _fake_record(
        [_fake_alignment(accession="ACC", hit_def="x", identities=18, align_length=20)]
    )
    patch_ncbi((record, "RID"))
    out = await find_offtargets(FindOfftargetsInput(guide=_GUIDE))
    assert out.hits[0].mismatch_count == 2
    assert out.hits[0].risk_label == "high"


async def test_3_mismatches_is_medium(patch_ncbi) -> None:
    record = _fake_record(
        [_fake_alignment(accession="ACC", hit_def="x", identities=17, align_length=20)]
    )
    patch_ncbi((record, "RID"))
    out = await find_offtargets(FindOfftargetsInput(guide=_GUIDE))
    assert out.hits[0].mismatch_count == 3
    assert out.hits[0].risk_label == "medium"


async def test_4_mismatches_is_low(patch_ncbi) -> None:
    record = _fake_record(
        [_fake_alignment(accession="ACC", hit_def="x", identities=16, align_length=20)]
    )
    patch_ncbi((record, "RID"))
    out = await find_offtargets(FindOfftargetsInput(guide=_GUIDE))
    assert out.hits[0].mismatch_count == 4
    assert out.hits[0].risk_label == "low"


async def test_above_max_mismatches_excluded(patch_ncbi) -> None:
    record = _fake_record(
        [_fake_alignment(accession="ACC", hit_def="x", identities=10, align_length=20)]
    )
    patch_ncbi((record, "RID"))
    out = await find_offtargets(FindOfftargetsInput(guide=_GUIDE, max_mismatches=4))
    assert out.num_offtargets_returned == 0


async def test_partial_alignment_low_coverage_is_low_risk(patch_ncbi) -> None:
    # 15/15 perfect identity but only 15 of 20 nt aligned (75% coverage)
    record = _fake_record(
        [_fake_alignment(accession="ACC", hit_def="x", identities=15, align_length=15)]
    )
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
    await find_offtargets(
        FindOfftargetsInput(guide=_GUIDE, database="refseq_genomic")
    )
    assert patch_ncbi.calls[0]["database"] == "refseq_genomic"


async def test_propagates_high_expect_threshold_for_short_queries(patch_ncbi) -> None:
    """find_offtargets defaults to E=1000 for short queries; that should flow through."""
    patch_ncbi((_fake_record([]), "RID"))
    await find_offtargets(FindOfftargetsInput(guide=_GUIDE))
    assert patch_ncbi.calls[0]["expect"] >= 100.0


# --- Sorting + capping --------------------------------------------------------------


async def test_hits_sorted_high_then_low_risk(patch_ncbi) -> None:
    record = _fake_record(
        [
            _fake_alignment(accession="LOW", hit_def="x", identities=16, align_length=20),
            _fake_alignment(accession="HIGH", hit_def="x", identities=20, align_length=20),
            _fake_alignment(accession="MED", hit_def="x", identities=17, align_length=20),
        ]
    )
    patch_ncbi((record, "RID"))
    out = await find_offtargets(FindOfftargetsInput(guide=_GUIDE))
    accessions = [h.accession for h in out.hits]
    assert accessions == ["HIGH", "MED", "LOW"]
    assert out.high_risk_count == 1
    assert out.medium_risk_count == 1
    assert out.low_risk_count == 1


async def test_max_hits_caps_response(patch_ncbi) -> None:
    record = _fake_record(
        [
            _fake_alignment(accession=f"ACC{i}", hit_def="x", identities=20, align_length=20)
            for i in range(10)
        ]
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
    assert "seed" in text  # mentions seed-region weighting limitation
    assert "bulge" in text or "indel" in text  # mentions bulge/indel limitation


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
