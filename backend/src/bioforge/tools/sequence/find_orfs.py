"""Open reading frame (ORF) finder.

Scans up to six frames (configurable) of a DNA sequence, finds ATG-initiated segments
that run to an in-frame stop codon (or to sequence end if `require_stop=False`), and
returns those whose protein length meets `min_length_aa`. Each ORF carries its frame,
DNA coordinates (relative to the forward strand of the input, regardless of the strand
the ORF lives on), translated protein, and the raw DNA span.

Coordinate convention: `dna_start` / `dna_end` are 0-based half-open intervals over the
INPUT forward strand. For ORFs found on the reverse-complement strand, `dna_start` is
still the lower coordinate on the forward strand — i.e. the position of the ORF's 3' end
on the forward strand corresponds to the ORF's 5' start.
"""

from __future__ import annotations

from typing import Literal

from Bio.Seq import Seq
from pydantic import BaseModel, Field, field_validator

from bioforge.tools.base import ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

_DNA_CHARS = set("ACGTNacgtn")
_STOP_CODONS_STANDARD = {"TAA", "TAG", "TGA"}


class FindOrfsInput(ToolInput):
    sequence: str = Field(
        ..., min_length=3, description="DNA sequence (A/C/G/T/N, case-insensitive)."
    )
    min_length_aa: int = Field(
        default=50,
        ge=1,
        le=10_000,
        description=(
            "Minimum protein length (amino acids) for an ORF to be reported. Lower this "
            "for short ORFs (e.g. peptide hormones at ~10-30 aa); default of 50 is the "
            "common 'definitely-a-real-ORF' threshold."
        ),
    )
    frames: list[Literal[1, 2, 3, -1, -2, -3]] = Field(
        default_factory=lambda: [1, 2, 3, -1, -2, -3],
        description=(
            "Which reading frames to scan. Default = all six. Limit to a subset (e.g. "
            "[1,2,3]) when you only want forward-strand ORFs."
        ),
        min_length=1,
        max_length=6,
    )
    genetic_code: int = Field(
        default=1,
        ge=1,
        le=33,
        description="NCBI genetic-code table ID. Default = 1 (standard).",
    )
    require_stop: bool = Field(
        default=True,
        description=(
            "If true (default), only report ORFs that terminate at an in-frame stop "
            "codon. If false, ORFs that run to the sequence end without a stop are also "
            "reported (e.g. fragmentary sequences)."
        ),
    )
    max_orfs: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Cap the response size. Returns the LONGEST `max_orfs` ORFs.",
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


class Orf(BaseModel):
    frame: int = Field(description="Reading frame: 1/2/3 forward, -1/-2/-3 reverse complement.")
    strand: Literal["+", "-"]
    dna_start: int = Field(description="0-based start on the input forward strand.")
    dna_end: int = Field(description="0-based exclusive end on the input forward strand.")
    length_nt: int
    length_aa: int
    protein: str = Field(description="Translated protein, NOT including a trailing '*'.")
    dna: str = Field(description="ORF DNA on its own strand, 5'→3' (ATG-initiated).")
    has_stop: bool


class FindOrfsOutput(ToolOutput):
    num_orfs: int
    longest_orf_length_aa: int
    sequence_length: int
    orfs: list[Orf]


def _scan_frame(
    fwd_seq: str, frame: int, table: int, min_length_aa: int, require_stop: bool
) -> list[Orf]:
    """Walk one frame, emit each ATG..STOP segment that passes the length filter.

    Coordinates returned are on the FORWARD strand of the input (`fwd_seq`), regardless
    of whether this frame is reverse.
    """
    strand_seq = fwd_seq if frame > 0 else str(Seq(fwd_seq).reverse_complement())
    offset = (abs(frame) - 1)
    n = len(strand_seq)
    orfs: list[Orf] = []

    i = offset
    current_start: int | None = None
    while i + 3 <= n:
        codon = strand_seq[i : i + 3]
        if current_start is None:
            if codon == "ATG":
                current_start = i
            i += 3
            continue
        if codon in _STOP_CODONS_STANDARD:
            # Emit ORF [current_start, i) on strand_seq, exclusive of stop codon.
            dna_strand_segment = strand_seq[current_start:i]
            protein = str(
                Seq(dna_strand_segment).translate(table=table, to_stop=False)
            )
            if len(protein) >= min_length_aa:
                orfs.append(
                    _build_orf(
                        frame=frame,
                        strand_seq_len=n,
                        strand_start=current_start,
                        strand_end=i,
                        protein=protein,
                        dna_segment=dna_strand_segment,
                        has_stop=True,
                    )
                )
            current_start = None
        i += 3

    # Handle ORF that ran to the end without a stop.
    if current_start is not None and not require_stop:
        # Trim trailing partial codon, if any.
        end_on_strand = current_start + ((n - current_start) // 3) * 3
        if end_on_strand > current_start:
            dna_segment = strand_seq[current_start:end_on_strand]
            protein = str(Seq(dna_segment).translate(table=table, to_stop=False))
            if len(protein) >= min_length_aa:
                orfs.append(
                    _build_orf(
                        frame=frame,
                        strand_seq_len=n,
                        strand_start=current_start,
                        strand_end=end_on_strand,
                        protein=protein,
                        dna_segment=dna_segment,
                        has_stop=False,
                    )
                )

    return orfs


def _build_orf(
    *,
    frame: int,
    strand_seq_len: int,
    strand_start: int,
    strand_end: int,
    protein: str,
    dna_segment: str,
    has_stop: bool,
) -> Orf:
    if frame > 0:
        fwd_start, fwd_end = strand_start, strand_end
        strand = "+"
    else:
        # Mirror coordinates back onto the forward strand.
        fwd_start = strand_seq_len - strand_end
        fwd_end = strand_seq_len - strand_start
        strand = "-"
    return Orf(
        frame=frame,
        strand=strand,
        dna_start=fwd_start,
        dna_end=fwd_end,
        length_nt=len(dna_segment),
        length_aa=len(protein),
        protein=protein,
        dna=dna_segment,
        has_stop=has_stop,
    )


@register_tool(
    name="find_orfs",
    description=(
        "Find open reading frames (ATG → STOP) in a DNA sequence across some or all six "
        "reading frames. Returns each qualifying ORF with its frame, strand, forward-"
        "strand DNA coordinates, translated protein, and ORF DNA. Use when the user "
        "asks for ORFs, candidate proteins from a DNA fragment, or wants to know where "
        "a gene might lie in unannotated sequence. The default 50-aa minimum length "
        "filter excludes the dense noise of short spurious ORFs; lower it for short-"
        "peptide work."
    ),
    input_model=FindOrfsInput,
    output_model=FindOrfsOutput,
    version="1.0.0",
    citations=[
        "Biopython Bio.Seq.translate",
        "NCBI Genetic Codes (https://www.ncbi.nlm.nih.gov/Taxonomy/Utils/wprintgc.cgi)",
    ],
    cost_hint="cheap",
    destructive=False,
    tags=["sequence", "annotation"],
)
async def find_orfs(inp: FindOrfsInput) -> FindOrfsOutput:
    fwd = inp.sequence  # already uppercased and validated
    orfs: list[Orf] = []
    for frame in inp.frames:
        orfs.extend(
            _scan_frame(
                fwd_seq=fwd,
                frame=frame,
                table=inp.genetic_code,
                min_length_aa=inp.min_length_aa,
                require_stop=inp.require_stop,
            )
        )

    orfs.sort(key=lambda o: o.length_aa, reverse=True)
    orfs = orfs[: inp.max_orfs]

    return FindOrfsOutput(
        num_orfs=len(orfs),
        longest_orf_length_aa=orfs[0].length_aa if orfs else 0,
        sequence_length=len(fwd),
        orfs=orfs,
    )
