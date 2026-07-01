"""Build a phylogenetic tree (neighbor-joining or UPGMA) from aligned sequences.

Pure Python via `Bio.Phylo.TreeConstruction` — no subprocess, no external binary.
Unlike `align_msa` (which shells out to MAFFT) or `amr_detection` (which shells
out to blastx), this tool has zero external runtime dependencies beyond
Biopython itself, so it has no "not configured" failure mode.

Three ways to supply input, exactly one per call:
  - `sequences`   — a list of {id, sequence} pairs. Must already be the SAME
                    LENGTH (i.e. already aligned) — this tool does not align
                    anything itself. Unaligned sequences of differing lengths
                    raise ToolError pointing at align_msa rather than silently
                    guessing an alignment.
  - `aligned_fasta` — pre-aligned sequences as raw FASTA text (gap characters
                    included).
  - `aligned`     — field-compatible with `align_msa`'s `aligned` output list
                    ({id, aligned_sequence} pairs), so its output can be piped
                    straight in without reshaping.

**Silent-corruption guard:** Biopython's `DistanceCalculator` does NOT
validate that a chosen substitution model matches the sequence alphabet — a
protein matrix (e.g. `blosum62`) applied to DNA sequences returns a real
number with no error, because A/C/G/T all happen to also be valid single-
letter amino acid codes. This tool checks the model against a DNA/protein
sniff of the input and refuses the combination if they disagree (unless
`model='identity'`, which is alphabet-agnostic), rather than silently
returning a distance matrix computed with the wrong biology.
"""

from __future__ import annotations

import asyncio
import io
from enum import Enum

from Bio import Phylo
from Bio.Align import MultipleSeqAlignment
from Bio.Phylo.TreeConstruction import DistanceCalculator, DistanceTreeConstructor
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord
from pydantic import BaseModel, Field, field_validator, model_validator

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

_DNA_RESIDUES = set("ACGTUNacgtun")


class TreeMethod(str, Enum):
    nj = "nj"
    upgma = "upgma"


def _validate_seq_id(v: str) -> str:
    cleaned = v.strip()
    if not cleaned:
        raise ValueError("id is empty after stripping whitespace")
    if any(c.isspace() for c in cleaned) or ">" in cleaned:
        raise ValueError(f"id must not contain whitespace or '>': {v!r}")
    return cleaned


class PhyloSequence(BaseModel):
    id: str = Field(min_length=1, max_length=64, description="Unique sequence label.")
    sequence: str = Field(min_length=1, description="Residues, optionally gapped ('-') if already aligned.")

    @field_validator("id")
    @classmethod
    def _clean_id(cls, v: str) -> str:
        return _validate_seq_id(v)


class AlignedSequenceIn(BaseModel):
    """Field-compatible with align_msa's `AlignedSequence` output — its `aligned`
    list can be passed to this tool's `aligned` field with no reshaping."""

    id: str = Field(min_length=1, max_length=64)
    aligned_sequence: str = Field(min_length=1, description="Gapped, aligned residues.")

    @field_validator("id")
    @classmethod
    def _clean_id(cls, v: str) -> str:
        return _validate_seq_id(v)


class BuildPhylogeneticTreeInput(ToolInput):
    sequences: list[PhyloSequence] | None = Field(
        default=None,
        description=(
            "Named sequences. Must ALL be the same length (already aligned) — this tool does "
            "not align anything. If yours are unaligned, run align_msa first and pass its "
            "`aligned` output via the `aligned` field instead."
        ),
    )
    aligned_fasta: str | None = Field(
        default=None,
        description="Pre-aligned sequences as FASTA text, gap characters included. All records must be the same length.",
    )
    aligned: list[AlignedSequenceIn] | None = Field(
        default=None,
        description="align_msa's `aligned` output field, passed straight through.",
    )
    model: str = Field(
        default="identity",
        max_length=32,
        description=(
            "Distance model (Bio.Phylo.TreeConstruction.DistanceCalculator). 'identity' "
            "(default) is a simple alignment p-distance and works for DNA or protein. DNA-"
            "specific alternatives: 'blastn', 'trans', 'megablast'. Protein-specific "
            "alternatives: 'blosum62', 'pam250', 'blastp'. A protein model on DNA input (or "
            "vice versa) is rejected rather than silently computing a meaningless distance."
        ),
    )
    tree_method: TreeMethod = Field(
        default=TreeMethod.nj,
        description="'nj' (neighbor-joining, default — no molecular-clock assumption) or 'upgma' (assumes a constant substitution rate across lineages).",
    )

    @model_validator(mode="after")
    def _validate_source(self) -> "BuildPhylogeneticTreeInput":
        provided = [
            name
            for name, val in [("sequences", self.sequences), ("aligned_fasta", self.aligned_fasta), ("aligned", self.aligned)]
            if val
        ]
        if not provided:
            raise ValueError("Provide exactly one of: sequences, aligned_fasta, aligned.")
        if len(provided) > 1:
            raise ValueError(f"Provide exactly one of: sequences, aligned_fasta, aligned — got data for {provided}.")
        return self


class BuildPhylogeneticTreeOutput(ToolOutput):
    tree_method: str
    model: str
    num_sequences: int
    alignment_length: int
    newick_string: str = Field(description="Standard Newick tree format. Special characters in leaf names are single-quote-escaped per the Newick spec.")
    ascii_tree: str = Field(description="Human-readable ASCII rendering of the tree topology (Bio.Phylo.draw_ascii).")
    leaf_names: list[str] = Field(description="Leaf/terminal names in tree order (not necessarily input order).")
    distance_matrix: dict[str, dict[str, float]] = Field(description="Symmetric pairwise distance matrix, {seq_id: {seq_id: distance}}, including the zero diagonal.")
    caveats: list[str] = Field(default_factory=list)


def _parse_fasta(text: str) -> list[tuple[str, str]]:
    """Minimal FASTA parser: returns [(id, sequence)] preserving order. The id is the first
    whitespace-delimited token of the header (mirrors align_msa's parser)."""
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


def _records_from_input(inp: BuildPhylogeneticTreeInput) -> list[tuple[str, str]]:
    if inp.sequences is not None:
        return [(s.id, s.sequence) for s in inp.sequences]
    if inp.aligned_fasta is not None:
        parsed = _parse_fasta(inp.aligned_fasta)
        if not parsed:
            raise ToolError("aligned_fasta contained no sequences (no '>' headers found).")
        empty_ids = [i for i, (id_, _) in enumerate(parsed) if not id_]
        if empty_ids:
            raise ToolError("aligned_fasta contains a record with an empty header (a bare '>' line).")
        return parsed
    return [(s.id, s.aligned_sequence) for s in inp.aligned]


def _looks_like_dna(records: list[tuple[str, str]]) -> bool:
    all_chars: set[str] = set()
    for _, seq in records:
        all_chars.update(seq)
    all_chars.discard("-")
    return bool(all_chars) and all_chars.issubset(_DNA_RESIDUES)


def _build_tree_sync(
    records: list[tuple[str, str]], model_name: str, tree_method: str
) -> tuple[str, str, list[str], dict[str, dict[str, float]]]:
    ids = [r[0] for r in records]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise ToolError(f"Duplicate sequence ids: {dupes}. Each sequence needs a unique id.")

    if len(records) < 2:
        raise ToolError("At least 2 sequences are required to build a tree.")

    lengths = {len(seq) for _, seq in records}
    if len(lengths) > 1:
        raise ToolError(
            f"Input sequences are not all the same length (lengths seen: {sorted(lengths)}). "
            "build_phylogenetic_tree requires an ALIGNMENT, not raw unaligned sequences — align "
            "them first (e.g. with align_msa) and pass the result via the `aligned` field, or "
            "supply sequences/aligned_fasta that are already equal-length."
        )

    if model_name not in DistanceCalculator.models:
        raise ToolError(f"Unknown model {model_name!r}. Available models: {DistanceCalculator.models}.")

    if model_name != "identity":
        is_dna = _looks_like_dna(records)
        if is_dna and model_name not in DistanceCalculator.dna_models:
            raise ToolError(
                f"model={model_name!r} is not a DNA/RNA distance model, but the input looks "
                f"like DNA/RNA. Use model='identity' or one of: {DistanceCalculator.dna_models}."
            )
        if not is_dna and model_name not in DistanceCalculator.protein_models:
            raise ToolError(
                f"model={model_name!r} is not a protein distance model, but the input looks "
                f"like protein. Use model='identity' or one of: {DistanceCalculator.protein_models}."
            )

    seq_records = [SeqRecord(Seq(seq), id=id_) for id_, seq in records]
    alignment = MultipleSeqAlignment(seq_records)

    try:
        calculator = DistanceCalculator(model_name)
    except ValueError as e:
        raise ToolError(str(e)) from e

    try:
        dm = calculator.get_distance(alignment)
    except Exception as e:
        raise ToolError(
            f"Distance calculation failed: {type(e).__name__}: {e}. This can happen if the "
            "chosen model's alphabet doesn't match the sequences, or if a sequence contains "
            "residues outside what the model expects."
        ) from e

    try:
        constructor = DistanceTreeConstructor(calculator, tree_method)
        tree = constructor.build_tree(alignment)
    except Exception as e:
        raise ToolError(f"Tree construction ({tree_method}) failed: {type(e).__name__}: {e}") from e

    newick_buf = io.StringIO()
    Phylo.write(tree, newick_buf, "newick")
    newick_string = newick_buf.getvalue().strip()

    ascii_buf = io.StringIO()
    Phylo.draw_ascii(tree, file=ascii_buf)
    ascii_tree = ascii_buf.getvalue()

    leaf_names = [c.name for c in tree.get_terminals()]

    names = dm.names
    distance_matrix = {row: {col: float(dm[row, col]) for col in names} for row in names}

    return newick_string, ascii_tree, leaf_names, distance_matrix


@register_tool(
    name="build_phylogenetic_tree",
    description=(
        "Build a phylogenetic tree from aligned sequences using neighbor-joining (default) or "
        "UPGMA. Use when the user asks to 'build a tree', 'phylogenetic analysis', 'how are "
        "these sequences related', or wants to visualize evolutionary relationships among "
        "homologs/orthologs/strains. Accepts already-aligned input in three interchangeable "
        "forms: a list of {id, sequence} pairs (all equal length), raw aligned FASTA text, or "
        "align_msa's `aligned` output passed straight through — chain align_msa first if you "
        "only have unaligned sequences. Pure Python (Bio.Phylo), no external binary required. "
        "Returns the tree as Newick and ASCII, plus the underlying pairwise distance matrix."
    ),
    input_model=BuildPhylogeneticTreeInput,
    output_model=BuildPhylogeneticTreeOutput,
    version="1.0.0",
    citations=[
        "Saitou N, Nei M (1987) The neighbor-joining method: a new method for reconstructing "
        "phylogenetic trees. Mol Biol Evol 4(4):406-425.",
        "Cock PJA et al. (2009) Biopython: freely available Python tools for computational "
        "molecular biology and bioinformatics. Bioinformatics 25(11):1422-1423.",
    ],
    cost_hint="moderate",
    tags=["functional", "phylogenetics", "tree", "evolution"],
)
async def build_phylogenetic_tree(inp: BuildPhylogeneticTreeInput) -> BuildPhylogeneticTreeOutput:
    records = _records_from_input(inp)
    alignment_length = len(records[0][1]) if records else 0

    loop = asyncio.get_event_loop()
    try:
        newick_string, ascii_tree, leaf_names, distance_matrix = await loop.run_in_executor(
            None, _build_tree_sync, records, inp.model, inp.tree_method.value
        )
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(f"build_phylogenetic_tree failed: {type(e).__name__}: {e}") from e

    caveats = [
        f"Distances computed with the '{inp.model}' model; tree topology and branch lengths "
        "are only as meaningful as that model is appropriate for these sequences.",
    ]
    if len(records) < 3:
        caveats.append(
            "Fewer than 3 sequences were supplied — the resulting tree has no internal "
            "branching topology to speak of (a real phylogeny needs at least 3 taxa)."
        )
    if inp.tree_method == TreeMethod.upgma:
        caveats.append(
            "UPGMA assumes a constant substitution rate (molecular clock) across all lineages; "
            "if that assumption doesn't hold for these sequences, prefer tree_method='nj'."
        )

    return BuildPhylogeneticTreeOutput(
        tree_method=inp.tree_method.value,
        model=inp.model,
        num_sequences=len(records),
        alignment_length=alignment_length,
        newick_string=newick_string,
        ascii_tree=ascii_tree,
        leaf_names=leaf_names,
        distance_matrix=distance_matrix,
        caveats=caveats,
    )
