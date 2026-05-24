from __future__ import annotations

from Bio.Seq import Seq
from pydantic import Field, field_validator

from bioforge.tools.base import ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

_DNA_CHARS = set("ACGTNacgtn")


class ReverseComplementInput(ToolInput):
    sequence: str = Field(
        ...,
        description=(
            "A DNA sequence as a raw string of nucleotides (A/C/G/T, optionally with N "
            "for ambiguous bases). FASTA headers and whitespace are not accepted — pass "
            "the bare sequence."
        ),
        min_length=1,
    )

    @field_validator("sequence")
    @classmethod
    def _validate_dna(cls, v: str) -> str:
        cleaned = "".join(v.split())
        if not cleaned:
            raise ValueError("sequence is empty after stripping whitespace")
        bad = set(cleaned) - _DNA_CHARS
        if bad:
            raise ValueError(
                f"sequence contains non-DNA characters: {sorted(bad)!r}. "
                "Expected only A/C/G/T/N (case-insensitive)."
            )
        return cleaned


class ReverseComplementOutput(ToolOutput):
    reverse_complement: str = Field(
        description="The reverse complement of the input, uppercase, 5'→3'."
    )
    length: int = Field(description="Length of the sequence.")


@register_tool(
    name="reverse_complement",
    description=(
        "Compute the reverse complement of a DNA sequence (uppercase, returned 5'→3'). "
        "Use this when the user asks for a reverse complement, the antisense strand, or "
        "the bottom strand of a DNA molecule. Ambiguous N bases map to N. The output is "
        "a plain DNA string the user can feed into other sequence tools."
    ),
    input_model=ReverseComplementInput,
    output_model=ReverseComplementOutput,
    version="1.0.0",
    citations=["Biopython Bio.Seq.reverse_complement"],
    cost_hint="cheap",
    destructive=False,
    tags=["sequence", "transformation"],
)
async def reverse_complement(inp: ReverseComplementInput) -> ReverseComplementOutput:
    rc = str(Seq(inp.sequence.upper()).reverse_complement())
    return ReverseComplementOutput(reverse_complement=rc, length=len(rc))
