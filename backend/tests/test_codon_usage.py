"""Tests for codon_usage."""

from __future__ import annotations

import pydantic
import pytest

from bioforge.tools.base import ToolError
from bioforge.tools.sequence.codon_usage import CodonUsageInput, codon_usage


async def test_counts_match_construction() -> None:
    # 4 ATG (M), 2 AAA (K), 1 TAA (*)  → 7 codons, 21 nt
    seq = "ATG" * 4 + "AAA" * 2 + "TAA"
    out = await codon_usage(CodonUsageInput(sequence=seq))
    assert out.total_codons == 7
    assert out.informative_codons == 7
    assert out.ambiguous_codons == 0
    assert out.leftover_nucleotides == 0

    # Index by codon for easy assertions
    by_codon = {c.codon: c for c in out.codons}
    assert by_codon["ATG"].count == 4
    assert by_codon["ATG"].amino_acid == "M"
    assert by_codon["AAA"].count == 2
    assert by_codon["AAA"].amino_acid == "K"
    assert by_codon["TAA"].count == 1
    assert by_codon["TAA"].amino_acid == "*"


async def test_fraction_among_synonyms() -> None:
    # 3 GCG and 1 GCC, both code for Alanine. Fractions: 3/4=0.75, 1/4=0.25.
    seq = "GCG" * 3 + "GCC"
    out = await codon_usage(CodonUsageInput(sequence=seq))
    by_codon = {c.codon: c for c in out.codons}
    assert by_codon["GCG"].fraction_of_aa == 0.75
    assert by_codon["GCC"].fraction_of_aa == 0.25
    # AA-level total counts
    assert out.amino_acid_counts["A"] == 4


async def test_lone_codon_has_fraction_one() -> None:
    out = await codon_usage(CodonUsageInput(sequence="ATG"))
    by_codon = {c.codon: c for c in out.codons}
    assert by_codon["ATG"].fraction_of_aa == 1.0


async def test_ambiguous_codons_separated_from_aa_fractions() -> None:
    # 2 ATG + 1 NNN. NNN is ambiguous → reported separately, NOT in AA fraction math.
    seq = "ATG" * 2 + "NNN"
    out = await codon_usage(CodonUsageInput(sequence=seq))
    assert out.ambiguous_codons == 1
    assert out.informative_codons == 2
    by_codon = {c.codon: c for c in out.codons}
    assert by_codon["NNN"].amino_acid == "X"
    assert by_codon["ATG"].fraction_of_aa == 1.0  # not diluted by NNN
    assert out.amino_acid_counts["M"] == 2
    assert out.amino_acid_counts["X"] == 1


async def test_leftover_nucleotides_reported() -> None:
    out = await codon_usage(CodonUsageInput(sequence="ATG" + "AT"))
    assert out.total_codons == 1
    assert out.leftover_nucleotides == 2


async def test_frame_offset_applied() -> None:
    # "XATGAAA" in frame=2 reads "ATGAAA" → 1 ATG + 1 AAA
    out = await codon_usage(CodonUsageInput(sequence="GATGAAA", frame=2))
    by_codon = {c.codon: c for c in out.codons}
    assert by_codon["ATG"].count == 1
    assert by_codon["AAA"].count == 1


async def test_no_codons_in_frame_errors() -> None:
    # "AT" alone fails the Pydantic min_length=3 check, so we use a 3-nt sequence in
    # frame=3 (offset=2, leaves "G", which leftover-trims to ""). That path is where
    # the handler raises ToolError.
    with pytest.raises(ToolError, match="no complete codons"):
        await codon_usage(CodonUsageInput(sequence="ATG", frame=3))


async def test_rejects_non_dna() -> None:
    with pytest.raises(pydantic.ValidationError, match="non-DNA"):
        CodonUsageInput(sequence="ATGZ")


async def test_is_registered() -> None:
    from bioforge.tools.registry import get_tool

    spec = get_tool("codon_usage")
    assert spec.cost_hint == "cheap"
    assert "composition" in spec.tags
