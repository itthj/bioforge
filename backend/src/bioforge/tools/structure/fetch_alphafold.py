"""Fetch an AlphaFold predicted structure from EMBL-EBI.

This is the first structural-biology tool — the start of Phase 4. It calls the
AlphaFold DB public REST API:

    https://alphafold.ebi.ac.uk/api/prediction/{uniprot_id}

Returns metadata (gene, organism, length, model URLs) plus the PDB text with
per-residue pLDDT confidence parsed out of the temperatureFactor column.

The frontend Mol* viewer renders the PDB text directly; the metadata + pLDDT
statistics are what the agent reasons over and shows in the trace. We don't
download the cif or the PAE image — the cif is much larger and Mol* handles
PDB fine for visualization; the PAE plot is a separate concern.

NOT EXPERIMENTAL — AlphaFold is a predicted model. The caveats list in the
output is mandatory and non-negotiable: high-pLDDT regions are reliable,
low-pLDDT regions are very uncertain (often intrinsically disordered), and
single-chain predictions miss multimer interfaces and conformational dynamics.
This is part of the "AI never fabricates biology" rule — every result that
looks like a structure carries a sign that says "this is a prediction".

Cost hint: cheap. One HTTP call to a metadata endpoint + one to the PDB file
(~1-2 s total, ~100-500 KB payload typically). No approval gate.
"""

from __future__ import annotations

import httpx
from pydantic import Field

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

ALPHAFOLD_API_BASE = "https://alphafold.ebi.ac.uk/api/prediction"

# pLDDT confidence bins, per the AlphaFold paper / EBI documentation:
#   - very high: pLDDT >= 90  (backbone & side-chains generally accurate)
#   - confident: 70 <= pLDDT < 90  (backbone accurate, side-chains less so)
#   - low:      50 <= pLDDT < 70  (low confidence, treat with caution)
#   - very low: pLDDT < 50  (often intrinsically disordered)
_PLDDT_BINS = ("very_high", "confident", "low", "very_low")


CAVEATS = [
    (
        "AlphaFold predictions are computational, not experimental. High-pLDDT "
        "regions (>=90) are reliable; low (<50) are very uncertain and often "
        "intrinsically disordered."
    ),
    (
        "The model represents one conformational state — typically the most "
        "stable folded form. Flexible regions, IDRs, and allosteric states are "
        "not captured."
    ),
    (
        "Single-chain predictions miss multimer interface effects. For complexes, "
        "use AlphaFold-Multimer (not currently integrated)."
    ),
    (
        "Not every UniProt entry has an AlphaFold prediction (very long sequences, "
        "non-canonical isoforms, non-standard residues)."
    ),
]


class FetchAlphaFoldInput(ToolInput):
    uniprot_id: str = Field(
        ...,
        pattern=r"^[A-Z0-9]+$",
        min_length=6,
        max_length=10,
        description=(
            "UniProt accession (uppercase letters and digits, 6-10 chars). "
            "Examples: P38398 (BRCA1, human), P04637 (TP53, human), "
            "P00533 (EGFR, human). Use the canonical accession, not the entry "
            "name — entry names like 'BRCA1_HUMAN' are not accepted."
        ),
    )
    include_pdb_text: bool = Field(
        default=True,
        description=(
            "Include the full PDB file text in the response so the frontend "
            "Mol* viewer can render it. Set False if you only need metadata + "
            "pLDDT statistics (saves agent context space)."
        ),
    )
    max_pdb_kb: int = Field(
        default=500,
        ge=10,
        le=5000,
        description=(
            "Maximum PDB file size to return in kilobytes. PDB files for "
            "long proteins can exceed 1 MB and quickly bloat the agent's "
            "context window. If the file exceeds this cap, the tool returns "
            "metadata + pLDDT stats but sets pdb_text=None and adds a caveat. "
            "Default 500 KB is enough for typical proteins (~700 residues)."
        ),
    )


class FetchAlphaFoldOutput(ToolOutput):
    uniprot_id: str
    entry_id: str = Field(description="AlphaFold entry ID (e.g. AF-P38398-F1).")
    organism: str | None
    gene: str | None
    uniprot_description: str | None
    length_residues: int
    average_plddt: float = Field(description="Mean pLDDT across all residues. Range 0-100.")
    plddt_distribution: dict[str, int] = Field(
        description=(
            "Residue counts per confidence bin. Keys: very_high (>=90), "
            "confident (70-89), low (50-69), very_low (<50). Sums to "
            "length_residues."
        )
    )
    pdb_url: str
    cif_url: str
    pae_image_url: str | None
    latest_version: int | None
    model_created_date: str | None
    pdb_text: str | None = Field(
        default=None,
        description=(
            "Full PDB file text (if include_pdb_text=True and file fits under "
            "max_pdb_kb). Pass this verbatim to a Mol* viewer for rendering."
        ),
    )
    caveats: list[str] = Field(default_factory=list)


# --- HTTP, factored out for test patching --------------------------------------------


async def _fetch_alphafold(uniprot_id: str) -> tuple[dict, str | None]:
    """Fetch metadata + PDB text from AlphaFold DB.

    Returns `(metadata_dict, pdb_text_or_none)`. Metadata is the first element
    of the JSON array AlphaFold returns. PDB text is None if the metadata
    response had no pdbUrl (rare — usually means a non-PDB-format prediction).

    Patched in tests so the suite never hits the network.

    Raises:
        ToolError: if the UniProt ID has no prediction (404) or the API is
            unreachable.
    """
    api_url = f"{ALPHAFOLD_API_BASE}/{uniprot_id}"
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            meta_resp = await client.get(api_url)
        except httpx.HTTPError as e:
            raise ToolError(
                f"AlphaFold API unreachable: {type(e).__name__}: {e}. "
                "Check network connectivity to https://alphafold.ebi.ac.uk."
            ) from e

        if meta_resp.status_code == 404:
            raise ToolError(
                f"No AlphaFold prediction available for UniProt {uniprot_id!r}. "
                "Possible reasons: the accession is invalid, the protein is too "
                "long for AlphaFold's standard pipeline, it has non-canonical "
                "residues, or it's a viral/synthetic entry outside the model "
                "organism proteomes. Verify the accession at "
                "https://www.uniprot.org/uniprotkb/" + uniprot_id
            )
        if meta_resp.status_code != 200:
            raise ToolError(
                f"AlphaFold API returned HTTP {meta_resp.status_code} for "
                f"UniProt {uniprot_id!r}: {meta_resp.text[:200]!r}"
            )

        try:
            payload = meta_resp.json()
        except ValueError as e:
            raise ToolError(f"AlphaFold API returned non-JSON body: {meta_resp.text[:200]!r}") from e

        if not isinstance(payload, list) or not payload:
            raise ToolError(f"AlphaFold API returned empty result for UniProt {uniprot_id!r}.")

        metadata = payload[0]
        pdb_url = metadata.get("pdbUrl")
        if not pdb_url:
            return metadata, None

        try:
            pdb_resp = await client.get(pdb_url)
        except httpx.HTTPError as e:
            raise ToolError(f"AlphaFold PDB download failed: {type(e).__name__}: {e}.") from e
        if pdb_resp.status_code != 200:
            raise ToolError(f"AlphaFold PDB URL returned HTTP {pdb_resp.status_code}: {pdb_url}")
        return metadata, pdb_resp.text


# --- PDB parsing ---------------------------------------------------------------------


def _parse_plddt_from_pdb(pdb_text: str) -> tuple[list[float], int]:
    """Extract per-residue pLDDT from PDB ATOM records.

    AlphaFold stores the per-residue pLDDT score in the temperatureFactor
    column (columns 61-66, fixed-width). One value per residue, so we pick the
    CA atom of each residue to avoid counting side-chain atoms multiple times.

    Returns `(plddt_per_residue, num_residues)`. If no CA atoms are found
    (malformed PDB), returns `([], 0)`.
    """
    plddt: list[float] = []
    for line in pdb_text.splitlines():
        if not line.startswith(("ATOM  ", "HETATM")):
            continue
        # PDB fixed-width: atom name is columns 13-16 (slice 12:16).
        atom_name = line[12:16].strip()
        if atom_name != "CA":
            continue
        # temperatureFactor: columns 61-66 (slice 60:66). Float, may be padded
        # with spaces. AlphaFold writes 0.00-100.00.
        if len(line) < 66:
            continue
        try:
            b_factor = float(line[60:66].strip())
        except ValueError:
            continue
        plddt.append(b_factor)
    return plddt, len(plddt)


def _summarize_plddt(plddt: list[float]) -> tuple[float, dict[str, int]]:
    """Mean pLDDT + bin counts. Both zero if input is empty."""
    if not plddt:
        return 0.0, {k: 0 for k in _PLDDT_BINS}
    bins = {k: 0 for k in _PLDDT_BINS}
    for v in plddt:
        if v >= 90:
            bins["very_high"] += 1
        elif v >= 70:
            bins["confident"] += 1
        elif v >= 50:
            bins["low"] += 1
        else:
            bins["very_low"] += 1
    avg = sum(plddt) / len(plddt)
    return round(avg, 2), bins


# --- Tool ----------------------------------------------------------------------------


@register_tool(
    name="fetch_alphafold_structure",
    description=(
        "Fetch a predicted 3D protein structure from AlphaFold DB by UniProt "
        "accession. Returns the PDB file text (for rendering in a viewer), plus "
        "metadata: gene, organism, length, and per-residue pLDDT confidence "
        "statistics. Use when the user asks for the 3D structure of a protein, "
        "or to inspect a folding prediction, or to compare confidence across "
        "regions of a protein. The result is a COMPUTATIONAL PREDICTION, not an "
        "experimental structure — the caveats list in the output is mandatory "
        "context for any interpretation."
    ),
    input_model=FetchAlphaFoldInput,
    output_model=FetchAlphaFoldOutput,
    version="1.0.0",
    citations=[
        "Jumper J et al. (2021) Highly accurate protein structure prediction with AlphaFold. Nature 596:583-589",
        "Varadi M et al. (2024) AlphaFold Protein Structure Database in 2024. Nucleic Acids Res 52(D1):D368-D375",
        "AlphaFold DB (https://alphafold.ebi.ac.uk)",
    ],
    cost_hint="cheap",
    destructive=False,
    tags=["structure", "alphafold", "protein"],
    reference_data_keys=["alphafold_db"],
)
async def fetch_alphafold_structure(inp: FetchAlphaFoldInput) -> FetchAlphaFoldOutput:
    metadata, pdb_text = await _fetch_alphafold(inp.uniprot_id)

    if pdb_text is None:
        raise ToolError(
            f"AlphaFold returned no PDB URL for UniProt {inp.uniprot_id!r}. "
            "The entry exists but the standard PDB format is unavailable; only "
            "cif/bcif formats are present. PDB fetching is required for the "
            "current viewer integration."
        )

    plddt, n_res = _parse_plddt_from_pdb(pdb_text)
    if n_res == 0:
        raise ToolError(
            f"Could not parse any CA atoms from the AlphaFold PDB for "
            f"UniProt {inp.uniprot_id!r}. The file may be corrupted or in an "
            "unexpected format."
        )

    avg_plddt, bin_counts = _summarize_plddt(plddt)

    # Size cap: PDB files for long proteins can exceed 1 MB. Cap to keep agent
    # context manageable. The metadata + pLDDT stats are always returned;
    # pdb_text is dropped if too big.
    caveats = list(CAVEATS)
    pdb_size_kb = len(pdb_text.encode("utf-8")) / 1024
    returned_pdb: str | None
    if not inp.include_pdb_text:
        returned_pdb = None
    elif pdb_size_kb > inp.max_pdb_kb:
        returned_pdb = None
        caveats.append(
            f"PDB file is {pdb_size_kb:.0f} KB, exceeding the {inp.max_pdb_kb} KB "
            "limit — text omitted from response. Increase max_pdb_kb or fetch "
            f"directly from {metadata.get('pdbUrl', '(no pdb url)')}."
        )
    else:
        returned_pdb = pdb_text

    return FetchAlphaFoldOutput(
        uniprot_id=inp.uniprot_id,
        entry_id=metadata.get("entryId", ""),
        organism=metadata.get("organismScientificName"),
        gene=metadata.get("gene"),
        uniprot_description=metadata.get("uniprotDescription"),
        length_residues=n_res,
        average_plddt=avg_plddt,
        plddt_distribution=bin_counts,
        pdb_url=metadata.get("pdbUrl", ""),
        cif_url=metadata.get("cifUrl", ""),
        pae_image_url=metadata.get("paeImageUrl"),
        latest_version=metadata.get("latestVersion"),
        model_created_date=metadata.get("modelCreatedDate"),
        pdb_text=returned_pdb,
        caveats=caveats,
    )
