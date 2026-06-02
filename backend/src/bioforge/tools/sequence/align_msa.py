"""Multiple-sequence alignment via MAFFT (section 3 / Phase 4).

`align_msa` aligns 2+ sequences with MAFFT, run OUT OF PROCESS in a digest-pinned core-only
container (or a local binary) -- see `models/mafft/`. MAFFT is a deterministic aligner, NOT a
trained predictor, so it emits no per-prediction uncertainty; the output is the alignment plus
metadata, with biological soundness checks applied before the result is accepted.

Honesty rails (section 0 / Layer 7):
  - There is no pure-Python fallback. When MAFFT is not configured the tool REFUSES with setup
    guidance -- it never fabricates an alignment.
  - The aligner must not invent biology: every returned row, with gaps removed, must equal its
    input sequence (case-insensitive), the same set of IDs must come back, and all rows must
    share one column count. A violation is a failure, not a finding.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from bioforge.config import settings
from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool
from bioforge.tools.sequence.models.mafft import (
    MafftError,
    MafftUnavailable,
    run_alignment,
)

# Permissive residue alphabet: nucleotides + amino acids (+ '*' stop, 'X'/'-' handled below).
# Gaps are NOT allowed in the INPUT (inputs are unaligned); MAFFT introduces them.
_ALLOWED_INPUT = set("ABCDEFGHIKLMNPQRSTVWYZUXabcdefghiklmnpqrstvwyzux*")


class MsaSequence(BaseModel):
    id: str = Field(min_length=1, max_length=64, description="Unique sequence label (FASTA header).")
    sequence: str = Field(min_length=1, description="Unaligned residues (DNA, RNA, or protein). No gaps.")

    @field_validator("id")
    @classmethod
    def _clean_id(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("id is empty after stripping whitespace")
        if any(c.isspace() for c in cleaned) or ">" in cleaned:
            raise ValueError(f"id must not contain whitespace or '>': {v!r}")
        return cleaned

    @field_validator("sequence")
    @classmethod
    def _clean_sequence(cls, v: str) -> str:
        cleaned = "".join(v.split()).upper()
        if not cleaned:
            raise ValueError("sequence is empty after stripping whitespace")
        bad = set(cleaned) - {c.upper() for c in _ALLOWED_INPUT}
        if bad:
            raise ValueError(f"sequence contains characters that are not residues: {sorted(bad)!r}")
        return cleaned


class AlignMsaInput(ToolInput):
    sequences: list[MsaSequence] = Field(
        min_length=2,
        max_length=500,
        description="The 2+ sequences to align. Each needs a unique id. Provide UNALIGNED sequences.",
    )

    @field_validator("sequences")
    @classmethod
    def _unique_ids(cls, v: list[MsaSequence]) -> list[MsaSequence]:
        ids = [s.id for s in v]
        if len(set(ids)) != len(ids):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"sequence ids must be unique; duplicates: {dupes}")
        return v


class AlignedSequence(BaseModel):
    id: str
    aligned_sequence: str = Field(description="The sequence with gap characters ('-') inserted by MAFFT.")


class AlignMsaOutput(ToolOutput):
    method: str = Field(description="Aligner + parameters, e.g. 'MAFFT (--auto)'.")
    num_sequences: int
    alignment_length: int = Field(description="Number of columns; identical for every aligned row.")
    aligned: list[AlignedSequence]
    notes: list[str] = Field(default_factory=list)


def _to_fasta(seqs: list[MsaSequence]) -> str:
    return "".join(f">{s.id}\n{s.sequence}\n" for s in seqs)


def _parse_fasta(text: str) -> list[tuple[str, str]]:
    """Minimal FASTA parser: returns [(id, sequence)] preserving order. The id is the first
    whitespace-delimited token of the header (MAFFT may append nothing, but be defensive)."""
    records: list[tuple[str, str]] = []
    cur_id: str | None = None
    chunks: list[str] = []
    for line in text.splitlines():
        if line.startswith(">"):
            if cur_id is not None:
                records.append((cur_id, "".join(chunks)))
            cur_id = line[1:].strip().split()[0] if line[1:].strip() else ""
            chunks = []
        elif cur_id is not None:
            chunks.append(line.strip())
    if cur_id is not None:
        records.append((cur_id, "".join(chunks)))
    return records


@register_tool(
    name="align_msa",
    description=(
        "Align 2 or more DNA/RNA/protein sequences into a multiple-sequence alignment (MSA) with "
        "MAFFT. Returns each sequence with gap characters inserted plus the alignment length. Use "
        "when the user wants to align several sequences, compare homologs/orthologs, build an MSA, "
        "or find conserved regions across sequences. Requires a configured MAFFT runtime "
        "(out-of-process, digest-pinned); it never fabricates an alignment when MAFFT is absent."
    ),
    input_model=AlignMsaInput,
    output_model=AlignMsaOutput,
    version="1.0.0",
    citations=[
        "Katoh K, Standley DM (2013) MAFFT multiple sequence alignment software version 7. "
        "Mol Biol Evol 30:772-780 (core MAFFT, BSD-3-Clause).",
    ],
    cost_hint="moderate",
    destructive=False,
    tags=["sequence", "alignment", "msa"],
    model_versions={"aligner": "MAFFT (--auto)"},
    emits_instance_uncertainty={"aligner": False},
    published_accuracy={
        "aligner": (
            "VERIFY: MAFFT is a deterministic progressive/iterative aligner, not a trained "
            "predictor with a standalone held-out accuracy figure."
        )
    },
    training_distribution={"note": "deterministic alignment algorithm, not a trained model"},
    reference_data_keys=[],
)
async def align_msa(inp: AlignMsaInput) -> AlignMsaOutput:
    if not settings.mafft_enabled:
        raise ToolError(
            "Multiple-sequence alignment (align_msa) is not enabled. Set BIOFORGE_MAFFT_ENABLED=true "
            "and configure a digest-pinned CORE-ONLY MAFFT image (BIOFORGE_MAFFT_DOCKER_IMAGE) or a "
            "local mafft binary -- see models/mafft/legacy/README.md. No alignment is faked."
        )

    fasta_in = _to_fasta(inp.sequences)
    try:
        aligned_fasta = run_alignment(fasta_in, settings)
    except MafftUnavailable as e:
        raise ToolError(str(e)) from e
    except MafftError as e:
        raise ToolError(f"MAFFT alignment failed: {e}") from e

    records = _parse_fasta(aligned_fasta)
    by_id = dict(records)

    # --- Biological soundness (section 0 / Layer 7): an aligner must not invent biology ---
    in_ids = [s.id for s in inp.sequences]
    if len(records) != len(in_ids) or set(by_id) != set(in_ids):
        raise ToolError(
            "MAFFT returned a different set of sequences than was submitted "
            f"(in: {sorted(in_ids)}, out: {sorted(by_id)}). Refusing the alignment."
        )
    lengths = {len(seq) for _id, seq in records}
    if len(lengths) != 1:
        raise ToolError(
            f"MAFFT returned ragged rows (column counts {sorted(lengths)}); a valid alignment has "
            "one column count for every row. Refusing the alignment."
        )
    alignment_length = lengths.pop()
    inputs_by_id = {s.id: s.sequence for s in inp.sequences}
    for sid, aligned in records:
        degapped = aligned.replace("-", "").upper()
        if degapped != inputs_by_id[sid].upper():
            raise ToolError(
                f"MAFFT altered residues for {sid!r}: the de-gapped alignment does not match the "
                "submitted sequence. Refusing the alignment rather than report a corrupted result."
            )

    aligned_out = [AlignedSequence(id=sid, aligned_sequence=by_id[sid]) for sid in in_ids]
    return AlignMsaOutput(
        method="MAFFT (--auto)",
        num_sequences=len(aligned_out),
        alignment_length=alignment_length,
        aligned=aligned_out,
        notes=[
            "Alignment by MAFFT (Katoh & Standley 2013, core BSD-3-Clause), strategy auto-selected "
            "by input size. Gap characters are '-'. Columns are not annotated with per-position "
            "conservation here.",
        ],
    )
