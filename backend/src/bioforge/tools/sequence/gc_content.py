from __future__ import annotations

from Bio.SeqUtils import gc_fraction
from pydantic import Field, field_validator

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

_DNA_CHARS = set("ACGTNacgtn")


class GcContentInput(ToolInput):
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
        cleaned = "".join(v.split())  # tolerate whitespace inside a single line
        if not cleaned:
            raise ValueError("sequence is empty after stripping whitespace")
        bad = set(cleaned) - _DNA_CHARS
        if bad:
            raise ValueError(
                f"sequence contains non-DNA characters: {sorted(bad)!r}. Expected only A/C/G/T/N (case-insensitive)."
            )
        return cleaned


class GcContentOutput(ToolOutput):
    gc_percent: float = Field(description="GC content as a percentage, 0.0–100.0.")
    gc_count: int = Field(description="Count of G and C bases.")
    total_length: int = Field(description="Length of the analyzed sequence.")
    n_count: int = Field(description="Count of ambiguous N bases (excluded from GC%).")


@register_tool(
    name="gc_content",
    description=(
        "Compute the GC content (percentage of G and C bases) of a DNA sequence. "
        "Use this when the user asks for GC content, GC%, GC composition, or wants to "
        "characterize the base composition of a DNA sequence. Ambiguous N bases are "
        "excluded from the percentage. Pass the bare sequence string — no FASTA headers."
    ),
    input_model=GcContentInput,
    output_model=GcContentOutput,
    version="1.0.0",
    citations=["Biopython Bio.SeqUtils.gc_fraction"],
    cost_hint="cheap",
    destructive=False,
    tags=["sequence", "composition"],
)
async def gc_content(inp: GcContentInput) -> GcContentOutput:
    seq = inp.sequence.upper()
    n_count = seq.count("N")
    informative_length = len(seq) - n_count
    if informative_length == 0:
        raise ToolError("Cannot compute GC content: sequence is entirely N (ambiguous bases).")
    # `ambiguous="remove"` excludes N bases from the denominator, so reported GC% is the
    # fraction of *informative* bases that are G or C. This matches the tool's docstring
    # ("Ambiguous N bases are excluded from the percentage") and is the convention
    # biologists expect when N-content is non-trivial. `ambiguous="ignore"` would count
    # N as not-GC and stay in the denominator — a different, more conservative number.
    fraction = gc_fraction(seq, ambiguous="remove")
    gc_count = seq.count("G") + seq.count("C")
    return GcContentOutput(
        gc_percent=round(fraction * 100.0, 6),
        gc_count=gc_count,
        total_length=len(seq),
        n_count=n_count,
    )
