"""DNA → protein translation.

Supports all six reading frames (1/2/3 forward, -1/-2/-3 reverse complement) and any of
the NCBI genetic-code tables (default = 1, standard). Trailing nucleotides that don't
form a complete codon are NEVER silently dropped — `leftover_nucleotides` is reported in
the output so the caller (and the agent's responder) knows the input wasn't perfectly
codon-aligned.
"""

from __future__ import annotations

from typing import Literal

from Bio.Seq import Seq
from pydantic import Field, field_validator

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

_DNA_CHARS = set("ACGTNacgtn")


class TranslateInput(ToolInput):
    sequence: str = Field(
        ...,
        description=(
            "DNA sequence as a raw string of A/C/G/T/N (case-insensitive). FASTA headers "
            "and whitespace are not accepted — pass the bare sequence."
        ),
        min_length=1,
    )
    frame: Literal[1, 2, 3, -1, -2, -3] = Field(
        default=1,
        description=(
            "Reading frame. 1/2/3 start at offset 0/1/2 on the forward strand. "
            "-1/-2/-3 do the same on the reverse complement strand."
        ),
    )
    genetic_code: int = Field(
        default=1,
        ge=1,
        le=33,
        description=(
            "NCBI genetic-code table ID. 1 = standard (default). 2 = vertebrate "
            "mitochondrial. 11 = bacterial / plant plastid. See "
            "https://www.ncbi.nlm.nih.gov/Taxonomy/Utils/wprintgc.cgi for the full list."
        ),
    )
    to_stop: bool = Field(
        default=False,
        description=(
            "If true, truncate the protein at the first in-frame stop codon (and do not "
            "include the '*' character). If false (default), translate the entire frame "
            "and mark stops with '*'."
        ),
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
                f"sequence contains non-DNA characters: {sorted(bad)!r}. Expected only A/C/G/T/N (case-insensitive)."
            )
        return cleaned


class TranslateOutput(ToolOutput):
    protein: str = Field(description="Translated protein sequence, uppercase 1-letter codes.")
    length_aa: int
    frame: int
    genetic_code: int
    leftover_nucleotides: int = Field(
        description=(
            "Number of trailing nucleotides that did NOT form a complete codon and "
            "were not translated. 0 if the (possibly offset-adjusted) input divided "
            "evenly into codons."
        )
    )
    first_stop_position_aa: int | None = Field(
        default=None,
        description=(
            "0-based position of the first stop codon in the protein, or null if no "
            "stop codon was reached. Useful even when `to_stop=False`."
        ),
    )


def _apply_frame(seq: str, frame: int) -> str:
    """Return the substring to translate given the frame argument."""
    if frame > 0:
        return seq[frame - 1 :]
    # Negative frame: reverse-complement first, then offset.
    rc = str(Seq(seq).reverse_complement())
    return rc[-frame - 1 :]


@register_tool(
    name="translate",
    description=(
        "Translate a DNA sequence to protein. Supports all six reading frames and the "
        "full set of NCBI genetic-code tables (default = standard code, table 1). Use "
        "when the user wants a protein sequence from DNA, or wants to examine a frame "
        "for stop codons or ORF boundaries. Trailing nucleotides that don't form a "
        "complete codon are reported in `leftover_nucleotides` — they are never silently "
        "dropped from the analysis."
    ),
    input_model=TranslateInput,
    output_model=TranslateOutput,
    version="1.0.0",
    citations=[
        "Biopython Bio.Seq.translate",
        "NCBI Genetic Codes (https://www.ncbi.nlm.nih.gov/Taxonomy/Utils/wprintgc.cgi)",
    ],
    cost_hint="cheap",
    destructive=False,
    tags=["sequence", "translation"],
)
async def translate(inp: TranslateInput) -> TranslateOutput:
    seq = inp.sequence.upper()
    framed = _apply_frame(seq, inp.frame)
    leftover = len(framed) % 3
    codon_aligned = framed[: len(framed) - leftover] if leftover else framed

    if not codon_aligned:
        raise ToolError(
            f"Frame {inp.frame} on a {len(seq)}-nt sequence leaves nothing to translate "
            f"after offset + codon alignment (leftover_nucleotides={leftover})."
        )

    try:
        protein_full = str(Seq(codon_aligned).translate(table=inp.genetic_code, to_stop=False))
    except Exception as e:  # noqa: BLE001
        raise ToolError(
            f"Translation failed: {type(e).__name__}: {e}. "
            "Check that genetic_code is a valid NCBI table ID for this organism."
        ) from e

    first_stop = protein_full.find("*")
    first_stop_position = first_stop if first_stop >= 0 else None

    if inp.to_stop:
        protein = protein_full[:first_stop] if first_stop >= 0 else protein_full
    else:
        protein = protein_full

    return TranslateOutput(
        protein=protein,
        length_aa=len(protein),
        frame=inp.frame,
        genetic_code=inp.genetic_code,
        leftover_nucleotides=leftover,
        first_stop_position_aa=first_stop_position,
    )
