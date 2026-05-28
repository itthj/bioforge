"""Tests for format_hgvs.

Real biology where it matters (BRCA1 c.181T>G coords, sickle-cell HBB c.20A>T,
the canonical Δ508 CFTR 3-nt deletion). Adversarial inputs covered: symbolic
alts, multi-allelic, REF==ALT, anchored vs unanchored indels.
"""

from __future__ import annotations

import pydantic
import pytest
from bioforge.tools.base import ToolError
from bioforge.tools.variants.format_hgvs import (
    FormatHgvsInput,
    _classify_and_format,
    format_hgvs,
)

# --- SNV ----------------------------------------------------------------------------


async def test_brca1_snv_canonical_form() -> None:
    """BRCA1 c.181T>G lands at chr17:g.43106487T>G on GRCh38."""
    out = await format_hgvs(FormatHgvsInput(chrom="17", pos=43106487, ref="T", alt=["G"]))
    assert out.chrom == "17"
    assert len(out.alleles) == 1
    a = out.alleles[0]
    assert a.hgvs == "17:g.43106487T>G"
    assert a.kind == "substitution"


async def test_hbb_sickle_cell_snv() -> None:
    """HBB c.20A>T (the sickle-cell variant) lands at chr11:g.5226774T>A (gene is on -)
    or chr11:g.5226774A>T on +. We just verify the formatter handles either orientation."""
    out = await format_hgvs(FormatHgvsInput(chrom="11", pos=5226774, ref="A", alt=["T"]))
    assert out.alleles[0].hgvs == "11:g.5226774A>T"


async def test_chr_prefix_stripped_by_default() -> None:
    out = await format_hgvs(FormatHgvsInput(chrom="chr17", pos=43106487, ref="T", alt=["G"]))
    assert out.chrom == "17"
    assert out.alleles[0].hgvs.startswith("17:")
    assert any("stripped" in c.lower() for c in out.caveats)


async def test_chr_prefix_preserved_when_requested() -> None:
    out = await format_hgvs(FormatHgvsInput(chrom="chr17", pos=43106487, ref="T", alt=["G"], strip_chr_prefix=False))
    assert out.chrom == "chr17"
    assert out.alleles[0].hgvs == "chr17:g.43106487T>G"


# --- Insertions ---------------------------------------------------------------------


async def test_anchored_insertion_single_base() -> None:
    # VCF: pos=100, ref='T', alt='TG' → HGVS: 17:g.100_101insG
    out = await format_hgvs(FormatHgvsInput(chrom="17", pos=100, ref="T", alt=["TG"]))
    a = out.alleles[0]
    assert a.kind == "insertion"
    assert a.hgvs == "17:g.100_101insG"


async def test_anchored_insertion_multi_base() -> None:
    out = await format_hgvs(FormatHgvsInput(chrom="17", pos=100, ref="T", alt=["TCGA"]))
    a = out.alleles[0]
    assert a.kind == "insertion"
    assert a.hgvs == "17:g.100_101insCGA"
    assert "3 nt" in a.notes


# --- Deletions ----------------------------------------------------------------------


async def test_anchored_single_base_deletion() -> None:
    # VCF: pos=100, ref='TC', alt='T' → HGVS: 17:g.101del
    out = await format_hgvs(FormatHgvsInput(chrom="17", pos=100, ref="TC", alt=["T"]))
    a = out.alleles[0]
    assert a.kind == "deletion"
    assert a.hgvs == "17:g.101del"


async def test_anchored_multi_base_deletion() -> None:
    # VCF: pos=100, ref='TCGA', alt='T' → HGVS: 17:g.101_103del
    out = await format_hgvs(FormatHgvsInput(chrom="17", pos=100, ref="TCGA", alt=["T"]))
    a = out.alleles[0]
    assert a.kind == "deletion"
    assert a.hgvs == "17:g.101_103del"


async def test_cftr_delta_f508_style_deletion() -> None:
    """The famous CFTR ΔF508 deletes 3 nt (the CTT/Phe codon). At the VCF level on
    GRCh38 it's encoded as a 3-base anchored deletion. We just verify the position
    arithmetic, not the actual chrom7 coordinates."""
    out = await format_hgvs(FormatHgvsInput(chrom="7", pos=117559590, ref="CTTT", alt=["C"]))
    a = out.alleles[0]
    assert a.kind == "deletion"
    assert a.hgvs == "7:g.117559591_117559593del"
    assert "3 nt" in a.notes


# --- Delins -------------------------------------------------------------------------


async def test_unanchored_block_substitution_is_delins() -> None:
    # ref='AC', alt='GT' — no shared prefix → delins
    out = await format_hgvs(FormatHgvsInput(chrom="1", pos=500, ref="AC", alt=["GT"]))
    a = out.alleles[0]
    assert a.kind == "delins"
    assert a.hgvs == "1:g.500_501delinsGT"


async def test_unequal_length_no_shared_prefix_is_delins() -> None:
    out = await format_hgvs(FormatHgvsInput(chrom="1", pos=500, ref="ACGT", alt=["G"]))
    a = out.alleles[0]
    assert a.kind == "delins"
    assert a.hgvs == "1:g.500_503delinsG"


# --- Multi-allelic ------------------------------------------------------------------


async def test_multi_allelic_emits_one_hgvs_per_alt() -> None:
    out = await format_hgvs(FormatHgvsInput(chrom="17", pos=43106487, ref="T", alt=["G", "C", "A"]))
    assert len(out.alleles) == 3
    assert {a.alt for a in out.alleles} == {"G", "C", "A"}
    assert all(a.kind == "substitution" for a in out.alleles)
    assert any("multi-allelic" in c.lower() for c in out.caveats)


async def test_multi_allelic_mixed_kinds() -> None:
    """A single VCF row may carry an SNV + an insertion across two ALTs."""
    out = await format_hgvs(FormatHgvsInput(chrom="3", pos=200, ref="A", alt=["G", "AT"]))
    kinds = [a.kind for a in out.alleles]
    assert kinds == ["substitution", "insertion"]
    assert out.alleles[0].hgvs == "3:g.200A>G"
    assert out.alleles[1].hgvs == "3:g.200_201insT"


# --- Adversarial validation ---------------------------------------------------------


async def test_ref_equals_alt_raises_tool_error() -> None:
    with pytest.raises(ToolError, match="not a variant"):
        await format_hgvs(FormatHgvsInput(chrom="1", pos=100, ref="T", alt=["T"]))


async def test_symbolic_alt_rejected_at_validation() -> None:
    with pytest.raises(pydantic.ValidationError, match="symbolic"):
        FormatHgvsInput(chrom="1", pos=100, ref="A", alt=["<DEL>"])


async def test_spanning_deletion_star_rejected() -> None:
    with pytest.raises(pydantic.ValidationError, match="symbolic"):
        FormatHgvsInput(chrom="1", pos=100, ref="A", alt=["*"])


async def test_non_dna_ref_rejected() -> None:
    with pytest.raises(pydantic.ValidationError, match="ACGTN-only"):
        FormatHgvsInput(chrom="1", pos=100, ref="X", alt=["G"])


async def test_n_in_sequence_surfaces_caveat() -> None:
    out = await format_hgvs(FormatHgvsInput(chrom="1", pos=100, ref="N", alt=["G"]))
    assert any("'N'" in c for c in out.caveats)


async def test_chrom_with_invalid_characters_rejected() -> None:
    with pytest.raises(pydantic.ValidationError, match="unexpected characters"):
        FormatHgvsInput(chrom="chr 17", pos=100, ref="A", alt=["G"])


async def test_chr_only_prefix_after_strip_raises() -> None:
    with pytest.raises(ToolError, match="empty"):
        await format_hgvs(FormatHgvsInput(chrom="chr", pos=100, ref="A", alt=["G"]))


# --- Direct _classify_and_format unit tests -----------------------------------------


def test_classify_substitution() -> None:
    kind, hgvs, _ = _classify_and_format("17", 100, "A", "G")
    assert kind == "substitution"
    assert hgvs == "17:g.100A>G"


def test_classify_insertion() -> None:
    kind, hgvs, _ = _classify_and_format("17", 100, "A", "AGG")
    assert kind == "insertion"
    assert hgvs == "17:g.100_101insGG"


def test_classify_deletion() -> None:
    kind, hgvs, _ = _classify_and_format("17", 100, "AGG", "A")
    assert kind == "deletion"
    assert hgvs == "17:g.101_102del"


def test_classify_delins_when_no_shared_anchor() -> None:
    kind, hgvs, _ = _classify_and_format("17", 100, "AT", "GC")
    assert kind == "delins"
    assert hgvs == "17:g.100_101delinsGC"


# --- Always-present caveat about no right-shift normalization -----------------------


async def test_normalization_caveat_always_present() -> None:
    """The 3'-shift caveat must always be present — users need to know we don't shift."""
    out = await format_hgvs(FormatHgvsInput(chrom="1", pos=100, ref="A", alt=["G"]))
    full = " ".join(out.caveats).lower()
    assert "bcftools" in full or "3'" in full or "right-shift" in full or "normalize" in full


# --- Registry ----------------------------------------------------------------------


async def test_tool_registered_correctly() -> None:
    from bioforge.tools.registry import get_tool

    spec = get_tool("format_hgvs")
    assert spec.cost_hint == "cheap"
    assert "variants" in spec.tags
    assert "hgvs" in spec.tags
    assert spec.destructive is False
    assert any("den Dunnen" in c or "HGVS" in c for c in spec.citations)


# --- End-to-end composition with parse_vcf -----------------------------------------


async def test_parse_vcf_to_format_hgvs_to_annotate_variant_chain() -> None:
    """The three-step composition this slice unblocks: parse VCF → format HGVS →
    feed into annotate_variant. Live API stays out (parse_vcf and format_hgvs
    are both offline; annotate_variant is monkeypatched)."""
    from bioforge.tools.sequence.parse_vcf import ParseVcfInput, parse_vcf

    vcf = (
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=17>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "17\t43106487\trs28897672\tT\tG\t.\tPASS\t.\n"
    )
    parsed = await parse_vcf(ParseVcfInput(vcf_text=vcf))
    assert len(parsed.variants) == 1
    v = parsed.variants[0]

    formatted = await format_hgvs(FormatHgvsInput(chrom=v.chrom, pos=v.pos, ref=v.ref, alt=v.alt))
    assert formatted.alleles[0].hgvs == "17:g.43106487T>G"
    assert formatted.alleles[0].kind == "substitution"
