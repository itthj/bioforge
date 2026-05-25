"""Tests for the translate tool.

Translations are verified against textbook codon assignments from the standard genetic
code (NCBI table 1) — the same source the tool uses, but these are unambiguous facts
of the universal genetic code, not biological claims the tool needs a real fixture for.
"""

from __future__ import annotations

import pydantic
import pytest
from bioforge.tools.base import ToolError
from bioforge.tools.sequence.translate import (
    TranslateInput,
    translate,
)


async def test_basic_forward_translation() -> None:
    # ATG → M, AAA → K, CTG → L, TAG → * (stop)
    out = await translate(TranslateInput(sequence="ATGAAACTGTAG"))
    assert out.protein == "MKL*"
    assert out.length_aa == 4
    assert out.leftover_nucleotides == 0
    assert out.first_stop_position_aa == 3


async def test_to_stop_truncates_at_first_stop() -> None:
    out = await translate(
        TranslateInput(sequence="ATGAAACTGTAGGCG", to_stop=True)
    )
    assert out.protein == "MKL"
    assert out.length_aa == 3
    # first_stop_position is reported even with to_stop=True
    assert out.first_stop_position_aa == 3


async def test_leftover_nucleotides_reported_not_swallowed() -> None:
    """Two trailing bases must be reported in `leftover_nucleotides`, not dropped silently."""
    # 12 nt = 4 codons + 0 leftover. Add 2 → 14 nt = 4 codons + 2 leftover.
    out = await translate(TranslateInput(sequence="ATGAAACTGTAG" + "GC"))
    assert out.length_aa == 4
    assert out.leftover_nucleotides == 2


async def test_frame_2_offsets_by_one() -> None:
    # Forward strand "AATGAAA" in frame 2 starts at offset 1: "ATGAAA" → MK
    out = await translate(TranslateInput(sequence="AATGAAA", frame=2))
    assert out.protein == "MK"
    assert out.frame == 2


async def test_negative_frame_reverse_complements() -> None:
    # rev-comp of "ATGAAA" is "TTTCAT". Frame -1 reads "TTTCAT" → FH
    out = await translate(TranslateInput(sequence="ATGAAA", frame=-1))
    assert out.protein == "FH"
    assert out.frame == -1


async def test_first_stop_position_none_when_no_stop() -> None:
    out = await translate(TranslateInput(sequence="ATGAAACTG"))
    assert out.protein == "MKL"
    assert out.first_stop_position_aa is None


async def test_mitochondrial_genetic_code_differs() -> None:
    """TGA is a STOP in standard code (table 1) but Trp (W) in vertebrate mt (table 2)."""
    seq = "ATGTGAGCT"
    standard = await translate(TranslateInput(sequence=seq, genetic_code=1))
    mito = await translate(TranslateInput(sequence=seq, genetic_code=2))
    assert standard.protein[1] == "*"
    assert mito.protein[1] == "W"


async def test_rejects_non_dna() -> None:
    with pytest.raises(pydantic.ValidationError, match="non-DNA"):
        TranslateInput(sequence="ATGZ")


async def test_rejects_invalid_genetic_code() -> None:
    with pytest.raises(pydantic.ValidationError):
        TranslateInput(sequence="ATG", genetic_code=99)


async def test_frame_with_no_complete_codons_errors() -> None:
    """A 1-nt sequence with frame=1 leaves 1 nt — no codons. Should ToolError, not return ''."""
    with pytest.raises(pydantic.ValidationError):
        # min_length=1 lets it through validation, but...
        TranslateInput(sequence="")  # actually rejected at empty check
    with pytest.raises(ToolError, match="leaves nothing to translate"):
        await translate(TranslateInput(sequence="AT"))


async def test_is_registered() -> None:
    from bioforge.tools.registry import get_tool

    spec = get_tool("translate")
    assert spec.cost_hint == "cheap"
    assert "translation" in spec.tags
