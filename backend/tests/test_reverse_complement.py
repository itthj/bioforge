from __future__ import annotations

import pydantic
import pytest
from bioforge.tools.sequence.reverse_complement import (
    ReverseComplementInput,
    reverse_complement,
)


async def test_basic_reverse_complement() -> None:
    out = await reverse_complement(ReverseComplementInput(sequence="ATGC"))
    assert out.reverse_complement == "GCAT"
    assert out.length == 4


async def test_palindrome_is_itself() -> None:
    out = await reverse_complement(ReverseComplementInput(sequence="GAATTC"))
    assert out.reverse_complement == "GAATTC"


async def test_case_insensitive_input_uppercase_output() -> None:
    out = await reverse_complement(ReverseComplementInput(sequence="atgcatgc"))
    assert out.reverse_complement == "GCATGCAT"


async def test_n_bases_complement_to_n() -> None:
    out = await reverse_complement(ReverseComplementInput(sequence="ATGNCAT"))
    assert out.reverse_complement == "ATGNCAT"


async def test_rejects_non_dna() -> None:
    with pytest.raises(pydantic.ValidationError, match="non-DNA"):
        ReverseComplementInput(sequence="ATGZ")


async def test_provenance_stamped_via_executor() -> None:
    from bioforge.tools.registry import execute_tool

    out = await execute_tool("reverse_complement", {"sequence": "ATGC"})
    assert out.tool_name == "reverse_complement"
    assert out.tool_version == "1.0.0"
    assert "Biopython" in " ".join(out.citations)
