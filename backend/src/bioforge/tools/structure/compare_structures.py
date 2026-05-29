"""Compare an experimental structure against the AlphaFold prediction.

A composite tool that answers "where does the AlphaFold prediction agree with
the experimental structure?". This is the most common follow-up to a structure
question — once a user sees the prediction, they want to know how trustworthy
it is for the specific protein.

The tool:
  1. Calls find_best_structure(uniprot_id, prefer='experimental') to get the
     experimental PDB.
  2. Calls fetch_alphafold_structure(uniprot_id) for the prediction.
  3. Returns both PDB texts + summary metadata for both, side-by-side.
  4. Identifies the residue-range overlap between the SIFTS-mapped experimental
     coverage and the full AlphaFold model.

We deliberately do NOT compute per-residue RMSD on the backend. That requires
3D superposition (Bio.PDB.SVDSuperimposer or CEAligner) and a sequence-aware
residue alignment — both substantial enough to live in their own slice. For
this slice, Mol*'s built-in superpose feature handles visual comparison on
the frontend (the user clicks "superpose" inside the embedded viewer).

If no experimental structure exists for the UniProt ID, the tool errors out
with a clear message — users wanting only the prediction should call
fetch_alphafold_structure directly.

Cost hint: cheap (three downstream HTTP calls in parallel: SIFTS, RCSB, AF).
"""

from __future__ import annotations

import asyncio

from pydantic import Field

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool
from bioforge.tools.structure.fetch_alphafold import (
    FetchAlphaFoldInput,
    FetchAlphaFoldOutput,
    fetch_alphafold_structure,
)
from bioforge.tools.structure.fetch_pdb import FetchPdbOutput
from bioforge.tools.structure.find_best import (
    FindBestStructureInput,
    find_best_structure,
)


class CompareStructuresInput(ToolInput):
    uniprot_id: str = Field(
        ...,
        pattern=r"^[A-Z0-9]+$",
        min_length=6,
        max_length=10,
        description="UniProt accession. Both the experimental fetch + the AlphaFold prediction use this ID.",
    )
    max_pdb_kb: int = Field(
        default=1000,
        ge=10,
        le=10_000,
        description="Cap applied to both child PDB files. Each card can drop its text independently.",
    )


class StructureOverlap(ToolOutput):
    """Residue-range relationship between the experimental coverage and the AlphaFold model."""

    experimental_start: int | None = Field(
        description="SIFTS-mapped start residue (1-based) of the experimental coverage."
    )
    experimental_end: int | None
    alphafold_length: int = Field(description="Number of residues in the AlphaFold model.")
    overlap_start: int | None = Field(
        description="First residue covered by BOTH the experimental structure and AlphaFold."
    )
    overlap_end: int | None
    overlap_residues: int = Field(description="Number of residues in the experimental ∩ AlphaFold overlap.")
    experimental_only_residues: int = Field(
        description="Residues in experimental that fall outside the AlphaFold length (rare, but happens for isoform mismatches)."
    )
    predicted_only_residues: int = Field(
        description="Residues in the AlphaFold model that are NOT covered by the experimental structure. Where the prediction is the only available 3D model."
    )


class CompareStructuresOutput(ToolOutput):
    uniprot_id: str
    experimental: FetchPdbOutput
    predicted: FetchAlphaFoldOutput
    overlap: StructureOverlap
    summary: str = Field(
        description=(
            "Human-readable summary of the comparison: which regions are "
            "experimentally validated vs prediction-only, average pLDDT in the "
            "overlap region (a proxy for 'where the prediction matches reality')."
        )
    )
    caveats: list[str] = Field(default_factory=list)


def _compute_overlap(
    *,
    exp_start: int | None,
    exp_end: int | None,
    af_length: int,
) -> StructureOverlap:
    """Build the StructureOverlap record from the inputs.

    AlphaFold always starts at residue 1 (canonical isoform). If the
    experimental SIFTS mapping is missing (older structures don't always
    populate it), we report zero overlap and let the caveat list explain why.
    """
    if exp_start is None or exp_end is None or exp_end < exp_start:
        return StructureOverlap(
            experimental_start=exp_start,
            experimental_end=exp_end,
            alphafold_length=af_length,
            overlap_start=None,
            overlap_end=None,
            overlap_residues=0,
            experimental_only_residues=0,
            predicted_only_residues=af_length,
        )
    overlap_start = max(1, exp_start)
    overlap_end = min(af_length, exp_end)
    if overlap_end < overlap_start:
        # Experimental coverage is outside the AlphaFold range entirely.
        return StructureOverlap(
            experimental_start=exp_start,
            experimental_end=exp_end,
            alphafold_length=af_length,
            overlap_start=None,
            overlap_end=None,
            overlap_residues=0,
            experimental_only_residues=exp_end - exp_start + 1,
            predicted_only_residues=af_length,
        )
    overlap_residues = overlap_end - overlap_start + 1
    exp_only = max(0, (exp_end - exp_start + 1) - overlap_residues)
    pred_only = af_length - overlap_residues
    return StructureOverlap(
        experimental_start=exp_start,
        experimental_end=exp_end,
        alphafold_length=af_length,
        overlap_start=overlap_start,
        overlap_end=overlap_end,
        overlap_residues=overlap_residues,
        experimental_only_residues=exp_only,
        predicted_only_residues=pred_only,
    )


@register_tool(
    name="compare_structures",
    description=(
        "Compare the experimental structure of a protein against its AlphaFold "
        "prediction. Fetches both, identifies which residue ranges overlap, "
        "and returns side-by-side metadata so the agent can describe where "
        "the prediction is experimentally validated, where it is the only "
        "available model, and where the experimental structure goes beyond "
        "the canonical isoform. Use whenever the user wants to compare "
        "predicted vs experimental, evaluate prediction trustworthiness, or "
        "extend an experimental fragment with the rest of the protein."
    ),
    input_model=CompareStructuresInput,
    output_model=CompareStructuresOutput,
    version="1.0.0",
    citations=[
        "Jumper J et al. (2021) Highly accurate protein structure prediction with AlphaFold. Nature 596:583-589",
        "Berman HM et al. (2000) The Protein Data Bank. Nucleic Acids Res 28:235-242",
        "Dana JM et al. (2019) SIFTS. Nucleic Acids Res 47(D1):D482-D489",
    ],
    cost_hint="cheap",
    destructive=False,
    tags=["structure", "composite", "comparison", "protein"],
    reference_data_keys=["sifts", "rcsb_pdb", "alphafold_db"],
)
async def compare_structures(inp: CompareStructuresInput) -> CompareStructuresOutput:
    # Fire the experimental + predicted fetches concurrently — they're independent.
    # find_best_structure with prefer='experimental' will raise ToolError if SIFTS
    # has no experimental coverage, which is the right behavior for compare.
    exp_task = asyncio.create_task(
        find_best_structure(
            FindBestStructureInput(
                uniprot_id=inp.uniprot_id,
                prefer="experimental",
                include_pdb_text=True,
                max_pdb_kb=inp.max_pdb_kb,
            )
        )
    )
    af_task = asyncio.create_task(
        fetch_alphafold_structure(
            FetchAlphaFoldInput(
                uniprot_id=inp.uniprot_id,
                include_pdb_text=True,
                max_pdb_kb=inp.max_pdb_kb,
            )
        )
    )
    try:
        exp_result, predicted = await asyncio.gather(exp_task, af_task)
    except ToolError:
        # Bubble up — already actionable. Cancel any leftover task.
        for t in (exp_task, af_task):
            if not t.done():
                t.cancel()
        raise

    experimental = exp_result.pdb_result
    if experimental is None:
        # Shouldn't happen with prefer='experimental' (it raises on no match),
        # but defensive guard for the contract.
        raise ToolError(
            f"find_best_structure returned no pdb_result for {inp.uniprot_id} "
            "in experimental mode — this is a contract bug, please report."
        )

    # Pull the SIFTS-mapped residue range from the top candidate. The composite
    # tool already populated experimental_candidates with at least one entry.
    top_candidate = exp_result.experimental_candidates[0] if exp_result.experimental_candidates else None
    exp_start = top_candidate.unp_start if top_candidate else None
    exp_end = top_candidate.unp_end if top_candidate else None

    overlap = _compute_overlap(
        exp_start=exp_start,
        exp_end=exp_end,
        af_length=predicted.length_residues,
    )

    # Summary string: where the prediction is validated, where it's the only model.
    parts = [
        f"Experimental structure: PDB {experimental.pdb_id} "
        f"({experimental.experimental_method or 'method unknown'}, "
        f"resolution {experimental.resolution_angstrom:.2f} Å)"
        if experimental.resolution_angstrom is not None
        else f"Experimental structure: PDB {experimental.pdb_id} ({experimental.experimental_method or 'method unknown'})",
        f"AlphaFold prediction: {predicted.entry_id}, mean pLDDT {predicted.average_plddt:.1f}",
    ]
    if overlap.overlap_residues > 0:
        parts.append(
            f"Overlap region: residues {overlap.overlap_start}-{overlap.overlap_end} "
            f"({overlap.overlap_residues} residues) — covered by both the experimental "
            "structure and the AlphaFold model. This is where the prediction can be "
            "validated against the experiment."
        )
    if overlap.predicted_only_residues > 0:
        parts.append(
            f"Prediction-only: {overlap.predicted_only_residues} residues are "
            "covered by AlphaFold but NOT by the experimental structure. The "
            "prediction is the only available 3D model for these regions."
        )
    if overlap.experimental_only_residues > 0:
        parts.append(
            f"Experimental-only: {overlap.experimental_only_residues} residues "
            "are in the experimental coverage but fall outside the AlphaFold "
            "model. This typically means the experimental structure is on a "
            "non-canonical isoform — interpret with caution."
        )
    summary = " ".join(parts)

    caveats: list[str] = [
        "Per-residue RMSD is not computed by this version. Use Mol*'s 'superpose' "
        "feature (after loading both structures into the same viewer) for a "
        "visual comparison.",
        "Comparison assumes the experimental structure is on the canonical "
        "UniProt isoform. SIFTS coverage of non-canonical isoforms can confuse "
        "the overlap calculation — verify the SIFTS unp_start/unp_end against "
        "the UniProt entry if the experimental_only residue count is non-zero.",
    ]
    if exp_start is None or exp_end is None:
        caveats.append(
            "SIFTS did not report unp_start/unp_end for the chosen experimental "
            "structure — overlap residue counts are 0 / 0 / full predicted length. "
            "Inspect the experimental coverage manually."
        )

    return CompareStructuresOutput(
        uniprot_id=inp.uniprot_id,
        experimental=experimental,
        predicted=predicted,
        overlap=overlap,
        summary=summary,
        caveats=caveats,
    )
