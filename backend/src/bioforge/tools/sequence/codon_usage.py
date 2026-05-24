"""Codon usage / frequency analysis.

Walks a DNA sequence in 3-nucleotide chunks within a chosen reading frame, counts each
codon, and reports both raw counts and normalized per-amino-acid frequencies. Codons
containing N are reported separately under `ambiguous_codons` so they don't pollute the
amino-acid frequency math.

Useful for: optimizing codon choice for heterologous expression, detecting horizontal
gene transfer (atypical codon usage relative to host), or characterizing a CDS prior to
sequence design.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal

from Bio.Data.CodonTable import unambiguous_dna_by_id
from pydantic import BaseModel, Field, field_validator

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

_DNA_CHARS = set("ACGTNacgtn")


class CodonUsageInput(ToolInput):
    sequence: str = Field(
        ..., min_length=3, description="DNA sequence (A/C/G/T/N, case-insensitive)."
    )
    frame: Literal[1, 2, 3] = Field(
        default=1,
        description="Reading frame: 1/2/3. Negative frames not supported here — reverse-complement first if you need them.",
    )
    genetic_code: int = Field(
        default=1,
        ge=1,
        le=33,
        description="NCBI genetic-code table ID. Default = 1 (standard).",
    )

    @field_validator("sequence")
    @classmethod
    def _validate_dna(cls, v: str) -> str:
        cleaned = "".join(v.split()).upper()
        if not cleaned:
            raise ValueError("sequence is empty after stripping whitespace")
        bad = set(cleaned) - {c.upper() for c in _DNA_CHARS}
        if bad:
            raise ValueError(f"sequence contains non-DNA characters: {sorted(bad)!r}")
        return cleaned


class CodonStat(BaseModel):
    codon: str
    amino_acid: str = Field(description="One-letter AA code, '*' for stop, 'X' for ambiguous.")
    count: int
    fraction_of_aa: float = Field(
        description=(
            "This codon's share among codons that translate to the SAME amino acid. "
            "0.0 when this is the only synonym of its AA in the sequence."
        )
    )


class CodonUsageOutput(ToolOutput):
    total_codons: int
    informative_codons: int = Field(
        description="Codons with no ambiguous bases — used as the denominator for fractions."
    )
    ambiguous_codons: int = Field(
        description="Codons containing N. Excluded from per-AA fractions."
    )
    leftover_nucleotides: int
    frame: int
    genetic_code: int
    codons: list[CodonStat]
    amino_acid_counts: dict[str, int]


def _translate_codon(codon: str, table: dict[str, str]) -> str:
    """Single-codon translation. Returns 'X' for codons containing N, '*' for stops."""
    if "N" in codon:
        return "X"
    return table.get(codon, "X")


@register_tool(
    name="codon_usage",
    description=(
        "Count codon usage in a DNA sequence's reading frame. Returns per-codon counts, "
        "per-codon fractions (share among synonyms of the same amino acid), and per-AA "
        "totals. Use when designing codon-optimized sequences for heterologous expression, "
        "comparing codon bias between organisms, or characterizing a CDS. Codons with "
        "ambiguous (N) bases are counted separately and excluded from fractions."
    ),
    input_model=CodonUsageInput,
    output_model=CodonUsageOutput,
    version="1.0.0",
    citations=[
        "Biopython Bio.Data.CodonTable",
        "NCBI Genetic Codes (https://www.ncbi.nlm.nih.gov/Taxonomy/Utils/wprintgc.cgi)",
    ],
    cost_hint="cheap",
    destructive=False,
    tags=["sequence", "composition"],
)
async def codon_usage(inp: CodonUsageInput) -> CodonUsageOutput:
    seq = inp.sequence
    offset = inp.frame - 1
    framed = seq[offset:]
    leftover = len(framed) % 3
    if leftover:
        framed = framed[:-leftover]
    if not framed:
        raise ToolError(
            f"Frame {inp.frame} on a {len(seq)}-nt sequence leaves no complete codons."
        )

    try:
        codon_to_aa = dict(unambiguous_dna_by_id[inp.genetic_code].forward_table)
        # Stops aren't in forward_table; add them explicitly.
        for stop in unambiguous_dna_by_id[inp.genetic_code].stop_codons:
            codon_to_aa[stop] = "*"
    except KeyError as e:
        raise ToolError(
            f"Unknown genetic_code table id: {inp.genetic_code}"
        ) from e

    counts: Counter[str] = Counter()
    aa_counts: Counter[str] = Counter()
    ambiguous = 0

    for i in range(0, len(framed), 3):
        codon = framed[i : i + 3]
        counts[codon] += 1
        if "N" in codon:
            ambiguous += 1
            aa_counts["X"] += 1
        else:
            aa = codon_to_aa.get(codon, "X")
            aa_counts[aa] += 1

    informative = sum(c for codon, c in counts.items() if "N" not in codon)

    # Compute per-AA fractions for non-ambiguous codons.
    aa_to_codon_counts: dict[str, list[tuple[str, int]]] = {}
    for codon, c in counts.items():
        if "N" in codon:
            continue
        aa = codon_to_aa.get(codon, "X")
        aa_to_codon_counts.setdefault(aa, []).append((codon, c))

    stats: list[CodonStat] = []
    for aa, codon_list in aa_to_codon_counts.items():
        aa_total = sum(c for _, c in codon_list)
        for codon, c in codon_list:
            stats.append(
                CodonStat(
                    codon=codon,
                    amino_acid=aa,
                    count=c,
                    fraction_of_aa=round(c / aa_total, 4) if aa_total else 0.0,
                )
            )

    # Surface ambiguous codons separately so the agent can decide whether to flag them.
    for codon, c in counts.items():
        if "N" in codon:
            stats.append(
                CodonStat(
                    codon=codon,
                    amino_acid="X",
                    count=c,
                    fraction_of_aa=0.0,
                )
            )

    stats.sort(key=lambda s: (s.amino_acid, -s.count))

    return CodonUsageOutput(
        total_codons=sum(counts.values()),
        informative_codons=informative,
        ambiguous_codons=ambiguous,
        leftover_nucleotides=leftover,
        frame=inp.frame,
        genetic_code=inp.genetic_code,
        codons=stats,
        amino_acid_counts=dict(aa_counts),
    )
