"""Fetch an experimental protein structure from the RCSB PDB.

Companion to fetch_alphafold_structure: that tool returns AI predictions; this
one returns experimentally-determined structures (X-ray crystallography, cryo-EM,
NMR). Together they cover both ground-truth structures (use when an experimental
structure exists for the protein in the right state) and AI predictions (use
when no experimental structure is available, or to compare alternative folds).

API endpoints:
  - Metadata: https://data.rcsb.org/rest/v1/core/entry/{pdb_id}
  - PDB file: https://files.rcsb.org/download/{pdb_id}.pdb

Cost hint: cheap. ~1 s for metadata + ~0.5-3 s for the PDB file (depends on
size). PDB files for large complexes can exceed 1 MB but the default 1 MB cap
keeps the agent's context reasonable.

The caveats list is mandatory and different from AlphaFold's: experimental
structures have their own pitfalls (resolution-dependent uncertainty, crystal
contacts, missing loops, B-factor interpretation) that the agent must surface
when reasoning about them.
"""

from __future__ import annotations

import httpx
from pydantic import Field

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

RCSB_API_BASE = "https://data.rcsb.org/rest/v1/core/entry"
RCSB_DOWNLOAD_BASE = "https://files.rcsb.org/download"

# Residue names that should be skipped when collecting ligand IDs from HETATM
# records — water + common buffer/cryoprotectant components.
_HETATM_BLOCKLIST = {
    "HOH",  # water
    "DOD",  # heavy water
    "WAT",
}


class FetchPdbInput(ToolInput):
    pdb_id: str = Field(
        ...,
        pattern=r"^[A-Za-z0-9]{4}$",
        description=(
            "Four-character PDB ID, case-insensitive. Examples: 4HHB (human "
            "deoxyhemoglobin), 1MX6 (BRCA1 BRCT domain), 1HSA (HLA-A2). "
            "Find IDs at https://www.rcsb.org/."
        ),
    )
    include_pdb_text: bool = Field(
        default=True,
        description=(
            "Include the full PDB file text so the frontend Mol* viewer can "
            "render it. Set False to save agent context space."
        ),
    )
    max_pdb_kb: int = Field(
        default=1000,
        ge=10,
        le=10_000,
        description=(
            "Cap on returned PDB file size in kilobytes. Large complexes can "
            "exceed 1 MB; the cap protects the agent's context window. When "
            "exceeded, metadata + parsed stats still return but pdb_text is "
            "dropped with a caveat pointing at the source URL."
        ),
    )


class FetchPdbOutput(ToolOutput):
    pdb_id: str
    title: str | None
    experimental_method: str | None = Field(description="X-RAY DIFFRACTION, ELECTRON MICROSCOPY, SOLUTION NMR, etc.")
    resolution_angstrom: float | None = Field(
        description=(
            "Resolution in Angstroms (X-ray, cryo-EM). None for NMR or where RCSB does not report a single value."
        )
    )
    deposit_date: str | None
    release_date: str | None
    revision_date: str | None
    keywords: str | None
    chain_ids: list[str] = Field(description="Distinct chain IDs found in ATOM records.")
    num_chains: int
    num_residues: int = Field(description="Total CA atoms across all chains.")
    residues_per_chain: dict[str, int]
    ligand_ids: list[str] = Field(
        description=(
            "Distinct HETATM residue names, excluding water (HOH/DOD/WAT). "
            "These are the small molecules / cofactors / metals in the model."
        )
    )
    mean_b_factor: float | None = Field(
        description=(
            "Mean B-factor across all CA atoms. Higher = more atomic motion or "
            "disorder. For X-ray structures, scale roughly correlates with "
            "thermal motion + crystallographic disorder; for cryo-EM, with "
            "local resolution."
        )
    )
    pdb_url: str
    cif_url: str
    pdb_text: str | None = Field(
        default=None,
        description=(
            "PDB-format text. Populated when the entry has a PDB representation "
            "AND include_pdb_text=True AND size <= max_pdb_kb. For mmCIF-only "
            "entries (very large complexes), see cif_text + structure_format."
        ),
    )
    cif_text: str | None = Field(
        default=None,
        description=(
            "mmCIF-format text. Populated only when the entry has NO legacy PDB "
            "file (typical for ribosomes, viral capsids, very large assemblies) "
            "and we fell back to CIF. Includes the same atomic data; both formats "
            "are renderable by Mol*."
        ),
    )
    structure_format: str = Field(
        default="pdb",
        description=(
            "Format of the returned structure text: 'pdb' (default) or 'cif' "
            "(fallback for mmCIF-only entries). The frontend uses this to "
            "tell Mol* which loader to invoke."
        ),
    )
    caveats: list[str] = Field(default_factory=list)


# --- HTTP, factored out for test patching --------------------------------------------


async def _fetch_pdb(pdb_id: str) -> tuple[dict, str | None, str]:
    """Fetch metadata JSON + structure text from RCSB.

    Returns `(metadata_dict, structure_text_or_none, format)` where `format` is
    "pdb" (preferred) or "cif" (fallback for entries too large for the legacy
    PDB format — ribosomes, viral capsids, very large complexes). Patched in tests.

    Tries the .pdb endpoint first. If RCSB returns 404 for the PDB file, falls
    back to the .cif endpoint — every entry has a CIF representation, so this
    rarely fails. If both fail, returns `(metadata, None, "pdb")` so the caller
    can render whatever metadata is available with a clear caveat.

    Raises:
        ToolError: on 404 from the metadata endpoint (entry doesn't exist),
            other HTTP errors, or network failures.
    """
    pdb_id_upper = pdb_id.upper()
    meta_url = f"{RCSB_API_BASE}/{pdb_id_upper}"
    pdb_url = f"{RCSB_DOWNLOAD_BASE}/{pdb_id_upper}.pdb"
    cif_url = f"{RCSB_DOWNLOAD_BASE}/{pdb_id_upper}.cif"
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            meta_resp = await client.get(meta_url)
        except httpx.HTTPError as e:
            raise ToolError(
                f"RCSB API unreachable: {type(e).__name__}: {e}. Check network connectivity to https://data.rcsb.org."
            ) from e

        if meta_resp.status_code == 404:
            raise ToolError(
                f"No PDB entry found for ID {pdb_id_upper!r}. PDB IDs are "
                "four-character codes (letters and digits). Search at "
                "https://www.rcsb.org/ to verify, or use fetch_alphafold_structure "
                "if you have a UniProt accession instead."
            )
        if meta_resp.status_code != 200:
            raise ToolError(
                f"RCSB API returned HTTP {meta_resp.status_code} for {pdb_id_upper!r}: {meta_resp.text[:200]!r}"
            )
        try:
            metadata = meta_resp.json()
        except ValueError as e:
            raise ToolError(f"RCSB API returned non-JSON body: {meta_resp.text[:200]!r}") from e

        # Try PDB first.
        try:
            pdb_resp = await client.get(pdb_url)
        except httpx.HTTPError as e:
            raise ToolError(f"RCSB PDB download failed: {type(e).__name__}: {e}.") from e

        if pdb_resp.status_code == 200:
            return metadata, pdb_resp.text, "pdb"

        if pdb_resp.status_code != 404:
            raise ToolError(f"RCSB PDB download returned HTTP {pdb_resp.status_code}: {pdb_url}")

        # PDB 404 → try CIF. Large complexes (ribosomes, capsids) are mmCIF-only.
        try:
            cif_resp = await client.get(cif_url)
        except httpx.HTTPError as e:
            raise ToolError(f"RCSB CIF fallback failed: {type(e).__name__}: {e}.") from e
        if cif_resp.status_code == 200:
            return metadata, cif_resp.text, "cif"
        if cif_resp.status_code != 404:
            raise ToolError(f"RCSB CIF download returned HTTP {cif_resp.status_code}: {cif_url}")
        # Both 404 — entry exists in the metadata DB but no downloadable structure.
        return metadata, None, "pdb"


# --- PDB parsing ---------------------------------------------------------------------


def _parse_pdb_structure_stats(pdb_text: str) -> dict:
    """Walk ATOM/HETATM records to compute chain set, residue counts, ligands,
    and mean B-factor.

    Returns a dict with keys: chain_ids, num_chains, num_residues,
    residues_per_chain, ligand_ids, mean_b_factor (None if no CA atoms found).
    """
    chains: list[str] = []  # ordered by first occurrence
    residues_per_chain: dict[str, int] = {}
    ligands: list[str] = []  # ordered by first occurrence
    b_factors: list[float] = []

    for line in pdb_text.splitlines():
        if line.startswith("ATOM  "):
            atom_name = line[12:16].strip()
            if atom_name != "CA":
                continue
            if len(line) < 66:
                continue
            chain = line[21:22].strip() or " "
            if chain not in residues_per_chain:
                chains.append(chain)
                residues_per_chain[chain] = 0
            residues_per_chain[chain] += 1
            try:
                b = float(line[60:66].strip())
                b_factors.append(b)
            except ValueError:
                pass
        elif line.startswith("HETATM"):
            if len(line) < 20:
                continue
            res_name = line[17:20].strip()
            if not res_name or res_name in _HETATM_BLOCKLIST:
                continue
            if res_name not in ligands:
                ligands.append(res_name)

    num_residues = sum(residues_per_chain.values())
    mean_b = round(sum(b_factors) / len(b_factors), 2) if b_factors else None
    return {
        "chain_ids": chains,
        "num_chains": len(chains),
        "num_residues": num_residues,
        "residues_per_chain": residues_per_chain,
        "ligand_ids": ligands,
        "mean_b_factor": mean_b,
    }


def _extract_metadata_fields(meta: dict) -> dict:
    """Pull the small subset of RCSB JSON we actually surface.

    RCSB's `/core/entry` returns deeply nested JSON. We extract just the fields
    the agent needs to reason — title, experimental method, resolution, dates,
    keywords — and ignore the rest. Defensive `.get()` everywhere because RCSB
    occasionally omits fields for legacy entries.
    """
    struct = meta.get("struct") or {}
    rcsb_info = meta.get("rcsb_entry_info") or {}
    acc_info = meta.get("rcsb_accession_info") or {}
    keywords_section = meta.get("struct_keywords") or {}
    exptl = meta.get("exptl") or []

    # Resolution: prefer rcsb_entry_info.resolution_combined (a list, take first),
    # fall back to scanning exptl records.
    res_combined = rcsb_info.get("resolution_combined")
    if isinstance(res_combined, list) and res_combined:
        try:
            resolution = float(res_combined[0])
        except (TypeError, ValueError):
            resolution = None
    else:
        resolution = None

    # Experimental method.
    method: str | None = None
    if isinstance(exptl, list) and exptl:
        first = exptl[0]
        if isinstance(first, dict):
            method = first.get("method")
    if not method:
        method = rcsb_info.get("experimental_method")

    return {
        "title": struct.get("title"),
        "experimental_method": method,
        "resolution_angstrom": resolution,
        "deposit_date": acc_info.get("deposit_date"),
        "release_date": acc_info.get("initial_release_date"),
        "revision_date": acc_info.get("revision_date"),
        "keywords": keywords_section.get("text") or keywords_section.get("pdbx_keywords"),
    }


def _build_caveats(meta_fields: dict, structure_stats: dict) -> list[str]:
    """Caveats are method-aware: X-ray, cryo-EM, and NMR each carry distinct
    interpretation pitfalls. We start with the universal caveats, then append
    method-specific ones."""
    caveats = [
        "Experimental structures capture one snapshot of conformational space — "
        "flexibility, alternative conformers, and induced-fit states may not be "
        "represented.",
        "Missing residues (disordered loops, flexible termini) are common; the "
        "model only covers atoms that could be assigned electron density / "
        "cryo-EM density / NMR restraints.",
    ]
    method = (meta_fields.get("experimental_method") or "").upper()
    resolution = meta_fields.get("resolution_angstrom")

    if "X-RAY" in method:
        if isinstance(resolution, int | float):
            if resolution > 3.0:
                caveats.append(
                    f"Resolution is {resolution:.2f} Å — at >3 Å, side-chain rotamers "
                    "and many loop conformations are not unambiguously determined. "
                    "Treat side-chain positions as low-confidence."
                )
            elif resolution > 2.5:
                caveats.append(
                    f"Resolution is {resolution:.2f} Å — backbone is well-defined, "
                    "but some side-chain positions and water placements are "
                    "uncertain."
                )
        caveats.append(
            "Crystal contacts can shift loop conformations and oligomeric state "
            "relative to the protein's solution behavior."
        )
    elif "ELECTRON MICROSCOPY" in method or "CRYO-EM" in method:
        if isinstance(resolution, int | float) and resolution > 4.0:
            caveats.append(
                f"Cryo-EM resolution {resolution:.2f} Å — backbone trace is reliable "
                "but side-chains may be poorly resolved. Higher-resolution "
                "reconstructions, if available, are preferable for side-chain analysis."
            )
        caveats.append("Cryo-EM B-factors reflect local resolution, not thermal motion per se.")
    elif "NMR" in method:
        caveats.append(
            "NMR ensembles represent the conformational distribution in solution. "
            "If only a single model was fetched, it is one representative state "
            "from the deposited ensemble."
        )

    if structure_stats["num_chains"] > 1:
        caveats.append(
            f"Model contains {structure_stats['num_chains']} chains — interpret "
            "interface contacts in the context of the deposited oligomeric state."
        )

    return caveats


# --- Tool ----------------------------------------------------------------------------


@register_tool(
    name="fetch_pdb_structure",
    description=(
        "Fetch an experimentally-determined 3D protein structure from the RCSB "
        "PDB by 4-character PDB ID (e.g. 4HHB for hemoglobin). Returns the PDB "
        "file text (for the 3D viewer) plus metadata: title, experimental "
        "method, resolution, chains, ligands, mean B-factor. Use when the user "
        "asks for a known experimental structure or names a PDB ID. For AI "
        "predictions when no experimental structure exists, use "
        "fetch_alphafold_structure instead. The caveats list reflects "
        "method-specific interpretation pitfalls (X-ray crystal contacts, "
        "cryo-EM local resolution, NMR ensemble averaging) and is mandatory "
        "context for any structural interpretation."
    ),
    input_model=FetchPdbInput,
    output_model=FetchPdbOutput,
    version="1.0.0",
    citations=[
        "Berman HM et al. (2000) The Protein Data Bank. Nucleic Acids Res 28:235-242",
        "Burley SK et al. (2023) RCSB Protein Data Bank (RCSB.org). Nucleic Acids Res 51(D1):D488-D508",
        "RCSB PDB Data API (https://data.rcsb.org)",
    ],
    cost_hint="cheap",
    destructive=False,
    tags=["structure", "pdb", "rcsb", "protein"],
)
async def fetch_pdb_structure(inp: FetchPdbInput) -> FetchPdbOutput:
    pdb_id_upper = inp.pdb_id.upper()
    metadata, structure_text, structure_format = await _fetch_pdb(pdb_id_upper)

    if structure_text is None:
        raise ToolError(
            f"RCSB entry {pdb_id_upper} exists but neither the PDB nor the mmCIF "
            "file could be downloaded. This is unusual — verify the entry status at "
            f"https://www.rcsb.org/structure/{pdb_id_upper}."
        )

    meta_fields = _extract_metadata_fields(metadata)

    # Atom-level stats are only computed for PDB-format text (the fixed-width
    # parser doesn't read mmCIF). For CIF-only entries we return the rendered
    # structure but null structural-stats fields, and explain why in a caveat.
    if structure_format == "pdb":
        structure_stats = _parse_pdb_structure_stats(structure_text)
        if structure_stats["num_residues"] == 0:
            raise ToolError(
                f"Could not parse any CA atoms from the RCSB PDB text for {pdb_id_upper!r}. "
                "The file may be DNA/RNA-only, ligand-only, or corrupted. Verify at "
                f"https://www.rcsb.org/structure/{pdb_id_upper}."
            )
        caveats = _build_caveats(meta_fields, structure_stats)
    else:
        # CIF fallback — the entry is too large for legacy PDB format. Skip
        # atom-level parsing; the 3D viewer still works, and the agent gets
        # explicit context that fine-grained stats aren't available.
        structure_stats = {
            "chain_ids": [],
            "num_chains": 0,
            "num_residues": 0,
            "residues_per_chain": {},
            "ligand_ids": [],
            "mean_b_factor": None,
        }
        caveats = _build_caveats(meta_fields, structure_stats)
        caveats.append(
            f"Entry {pdb_id_upper} has no legacy PDB-format file (typical for "
            "very large complexes — ribosomes, viral capsids, large multi-chain "
            "assemblies). Returning mmCIF format instead. Per-chain residue "
            "counts, ligand identification, and mean B-factor are not computed "
            "for CIF in this version — see the source at "
            f"{RCSB_DOWNLOAD_BASE}/{pdb_id_upper}.cif if you need them."
        )

    # Size cap — applies to both formats.
    size_kb = len(structure_text.encode("utf-8")) / 1024
    returned_pdb: str | None = None
    returned_cif: str | None = None
    if inp.include_pdb_text:
        if size_kb > inp.max_pdb_kb:
            caveats.append(
                f"{structure_format.upper()} file is {size_kb:.0f} KB, exceeding "
                f"the {inp.max_pdb_kb} KB limit — text omitted from response. "
                f"Increase max_pdb_kb or fetch directly from "
                f"{RCSB_DOWNLOAD_BASE}/{pdb_id_upper}.{structure_format}."
            )
        elif structure_format == "pdb":
            returned_pdb = structure_text
        else:
            returned_cif = structure_text

    return FetchPdbOutput(
        pdb_id=pdb_id_upper,
        title=meta_fields["title"],
        experimental_method=meta_fields["experimental_method"],
        resolution_angstrom=meta_fields["resolution_angstrom"],
        deposit_date=meta_fields["deposit_date"],
        release_date=meta_fields["release_date"],
        revision_date=meta_fields["revision_date"],
        keywords=meta_fields["keywords"],
        chain_ids=structure_stats["chain_ids"],
        num_chains=structure_stats["num_chains"],
        num_residues=structure_stats["num_residues"],
        residues_per_chain=structure_stats["residues_per_chain"],
        ligand_ids=structure_stats["ligand_ids"],
        mean_b_factor=structure_stats["mean_b_factor"],
        pdb_url=f"{RCSB_DOWNLOAD_BASE}/{pdb_id_upper}.pdb",
        cif_url=f"{RCSB_DOWNLOAD_BASE}/{pdb_id_upper}.cif",
        pdb_text=returned_pdb,
        cif_text=returned_cif,
        structure_format=structure_format,
        caveats=caveats,
    )
