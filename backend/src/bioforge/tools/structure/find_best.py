"""Find the best 3D structure for a UniProt accession.

Composite tool: instead of forcing the user (or the agent) to pick between
fetch_pdb_structure and fetch_alphafold_structure, this tool consults the
EBI SIFTS "best structures" mapping and returns:

  - An experimental PDB entry, if one covers the UniProt accession
    (preferring high resolution + large coverage — SIFTS already ranks
    these for us, we just take the first).
  - An AlphaFold prediction, if no experimental structure exists.

The chosen result is embedded under `pdb_result` or `alphafold_result` so
the frontend can render it with the appropriate card and the agent can
reason over the same metadata it would have gotten from a direct call.

SIFTS mapping endpoint:
    https://www.ebi.ac.uk/pdbe/api/mappings/best_structures/{uniprot_id}

The agent layer benefits because:
  - The planner can call ONE tool ("find the structure of BRCA1") instead
    of planning a tree of tries.
  - The decision reason is captured in `reason` so the agent's final
    response can explain WHY it picked one source over the other — that
    transparency is core to the "never fabricate biology" principle.

Cost hint: cheap (small SIFTS metadata call + one downstream tool call).
"""

from __future__ import annotations

from typing import Any, Literal

import httpx
from pydantic import Field

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool
from bioforge.tools.structure.fetch_alphafold import (
    FetchAlphaFoldInput,
    FetchAlphaFoldOutput,
    fetch_alphafold_structure,
)
from bioforge.tools.structure.fetch_pdb import (
    FetchPdbInput,
    FetchPdbOutput,
    fetch_pdb_structure,
)

SIFTS_BEST_STRUCTURES_URL = "https://www.ebi.ac.uk/pdbe/api/mappings/best_structures"

PreferOption = Literal["auto", "experimental", "predicted"]


class FindBestStructureInput(ToolInput):
    uniprot_id: str = Field(
        ...,
        pattern=r"^[A-Z0-9]+$",
        min_length=6,
        max_length=10,
        description=(
            "UniProt accession (uppercase letters and digits, 6-10 chars). "
            "Examples: P38398 (BRCA1), P04637 (TP53), P00533 (EGFR)."
        ),
    )
    prefer: PreferOption = Field(
        default="auto",
        description=(
            "Source preference. 'auto' (default): take the best experimental "
            "structure if SIFTS has one, else AlphaFold. 'experimental': fail "
            "if no experimental structure exists. 'predicted': always go "
            "directly to AlphaFold."
        ),
    )
    include_pdb_text: bool = Field(
        default=True,
        description="Forwarded to the chosen downstream tool. Same semantics as fetch_pdb / fetch_alphafold.",
    )
    max_pdb_kb: int = Field(
        default=1000,
        ge=10,
        le=10_000,
        description="Forwarded to the chosen downstream tool. Cap on returned PDB text size in kilobytes.",
    )


class ExperimentalCandidate(ToolOutput):
    """Trimmed-down SIFTS record for one candidate experimental structure.

    Inheriting ToolOutput is mildly odd (these are not first-class tool outputs),
    but it gives us the same `extra=allow` Pydantic config and keeps mypy happy
    when nested inside FindBestStructureOutput.
    """

    pdb_id: str
    chain_id: str | None
    coverage: float | None
    resolution_angstrom: float | None
    experimental_method: str | None
    unp_start: int | None
    unp_end: int | None


class FindBestStructureOutput(ToolOutput):
    uniprot_id: str
    source: Literal["experimental", "predicted"] = Field(
        description="'experimental' if a PDB structure was returned, 'predicted' if AlphaFold."
    )
    reason: str = Field(
        description=(
            "Human-readable explanation of why this source was chosen. The "
            "agent should surface this when summarizing the structural answer."
        )
    )
    experimental_candidates: list[ExperimentalCandidate] = Field(
        default_factory=list,
        description=(
            "Top experimental candidates SIFTS reported (up to 5). Empty if SIFTS "
            "had none. Even when the predicted source is chosen, this is populated "
            "with what was considered so the agent can explain trade-offs."
        ),
    )
    pdb_result: FetchPdbOutput | None = Field(
        default=None,
        description="Populated when source='experimental'. The full fetch_pdb_structure output.",
    )
    alphafold_result: FetchAlphaFoldOutput | None = Field(
        default=None,
        description="Populated when source='predicted'. The full fetch_alphafold_structure output.",
    )
    caveats: list[str] = Field(
        default_factory=list,
        description=(
            "Caveats specific to the structure-source decision (e.g. low coverage). "
            "The downstream tool's caveats stay nested on its result — the agent "
            "should surface BOTH lists."
        ),
    )


# --- SIFTS, factored out for test patching ------------------------------------------


async def _fetch_sifts_best_structures(uniprot_id: str) -> list[dict[str, Any]]:
    """Call the EBI SIFTS best-structures endpoint.

    Returns the list of candidate records (already sorted by SIFTS' scoring —
    primarily coverage, then resolution). Empty list if no experimental
    structure maps to this accession. Raises ToolError on transport failures
    other than 404 (404 we treat as "no experimental structures" and return [],
    which is biologically the right interpretation).

    Patched in tests so the suite never hits the network.
    """
    url = f"{SIFTS_BEST_STRUCTURES_URL}/{uniprot_id}"
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
        except httpx.HTTPError as e:
            raise ToolError(f"SIFTS API unreachable: {type(e).__name__}: {e}. Check https://www.ebi.ac.uk.") from e
    if resp.status_code == 404:
        # SIFTS returns 404 when the UniProt accession has no experimental
        # structure mappings — biologically valid, not an error.
        return []
    if resp.status_code != 200:
        raise ToolError(f"SIFTS API returned HTTP {resp.status_code} for {uniprot_id!r}: {resp.text[:200]!r}")
    try:
        payload = resp.json()
    except ValueError as e:
        raise ToolError(f"SIFTS API returned non-JSON body: {resp.text[:200]!r}") from e
    if not isinstance(payload, dict):
        raise ToolError(f"SIFTS API returned unexpected JSON shape: {type(payload).__name__}")
    records = payload.get(uniprot_id)
    if not isinstance(records, list):
        return []
    return records


# --- Helpers -----------------------------------------------------------------------


def _coerce_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _coerce_int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _records_to_candidates(records: list[dict[str, Any]], limit: int = 5) -> list[ExperimentalCandidate]:
    candidates: list[ExperimentalCandidate] = []
    for rec in records[:limit]:
        pdb_id = rec.get("pdb_id")
        if not isinstance(pdb_id, str):
            continue
        candidates.append(
            ExperimentalCandidate(
                pdb_id=pdb_id.upper(),
                chain_id=rec.get("chain_id"),
                coverage=_coerce_float(rec.get("coverage")),
                resolution_angstrom=_coerce_float(rec.get("resolution")),
                experimental_method=rec.get("experimental_method"),
                unp_start=_coerce_int(rec.get("unp_start")),
                unp_end=_coerce_int(rec.get("unp_end")),
            )
        )
    return candidates


def _format_pct(x: float | None) -> str:
    if x is None:
        return "?"
    return f"{x * 100:.0f}%"


def _format_resolution(x: float | None) -> str:
    return f"{x:.2f} Å" if x is not None else "no resolution"


# --- Tool ----------------------------------------------------------------------------


@register_tool(
    name="find_best_structure",
    description=(
        "Find the best available 3D structure for a UniProt accession — "
        "automatically chooses between an experimental PDB entry (preferred "
        "when available) and an AlphaFold prediction (fallback). Returns the "
        "full chosen structure plus the decision reason and the alternative "
        "candidates that were considered. Use this when the user asks for "
        "'the structure of <protein>' without specifying experimental vs "
        "predicted — it saves the agent from planning a tool-chain to handle "
        "the lookup-then-fallback logic. Underneath, calls SIFTS for the "
        "experimental mapping, then dispatches to fetch_pdb_structure or "
        "fetch_alphafold_structure."
    ),
    input_model=FindBestStructureInput,
    output_model=FindBestStructureOutput,
    version="1.0.0",
    citations=[
        "Dana JM et al. (2019) SIFTS: updated Structure Integration with Function, Taxonomy and Sequences resource. Nucleic Acids Res 47(D1):D482-D489",
        "EBI SIFTS API (https://www.ebi.ac.uk/pdbe/api/doc)",
    ],
    cost_hint="cheap",
    destructive=False,
    tags=["structure", "composite", "protein"],
)
async def find_best_structure(inp: FindBestStructureInput) -> FindBestStructureOutput:
    candidates: list[ExperimentalCandidate] = []

    # Hard-fast paths ---------------------------------------------------------
    if inp.prefer == "predicted":
        # User explicitly asked for the AI prediction; skip SIFTS.
        af = await fetch_alphafold_structure(
            FetchAlphaFoldInput(
                uniprot_id=inp.uniprot_id,
                include_pdb_text=inp.include_pdb_text,
                max_pdb_kb=inp.max_pdb_kb,
            )
        )
        return FindBestStructureOutput(
            uniprot_id=inp.uniprot_id,
            source="predicted",
            reason="User requested the predicted structure explicitly (prefer='predicted'). Skipped SIFTS mapping.",
            experimental_candidates=[],
            alphafold_result=af,
            caveats=[
                "An experimental structure may exist; this call did not look. "
                "Use prefer='auto' or prefer='experimental' to consult SIFTS first."
            ],
        )

    # SIFTS lookup ------------------------------------------------------------
    records = await _fetch_sifts_best_structures(inp.uniprot_id)
    candidates = _records_to_candidates(records)

    if not candidates:
        if inp.prefer == "experimental":
            raise ToolError(
                f"No experimental structure mapped to UniProt {inp.uniprot_id!r} "
                "in SIFTS. Switch to prefer='auto' to fall back to an AlphaFold "
                "prediction, or verify the accession at "
                "https://www.uniprot.org/uniprotkb/" + inp.uniprot_id
            )
        # Auto fallback to AlphaFold.
        af = await fetch_alphafold_structure(
            FetchAlphaFoldInput(
                uniprot_id=inp.uniprot_id,
                include_pdb_text=inp.include_pdb_text,
                max_pdb_kb=inp.max_pdb_kb,
            )
        )
        return FindBestStructureOutput(
            uniprot_id=inp.uniprot_id,
            source="predicted",
            reason=(
                f"No experimental structure mapped to UniProt {inp.uniprot_id} "
                "in SIFTS. Falling back to the AlphaFold prediction."
            ),
            experimental_candidates=[],
            alphafold_result=af,
            caveats=[
                "No experimental coverage was found via SIFTS — this is common for "
                "novel proteins, very large proteins, or proteins from organisms "
                "with limited structural biology coverage. The prediction is the "
                "only available 3D model."
            ],
        )

    # Take the top SIFTS hit. SIFTS already ranks by (coverage desc, resolution asc).
    top = candidates[0]
    try:
        pdb = await fetch_pdb_structure(
            FetchPdbInput(
                pdb_id=top.pdb_id,
                include_pdb_text=inp.include_pdb_text,
                max_pdb_kb=inp.max_pdb_kb,
            )
        )
    except ToolError as e:
        # The SIFTS entry exists but the actual PDB fetch failed — fall back to
        # AlphaFold in auto mode so we still give the agent something to work
        # with. In experimental mode, re-raise so the failure surfaces.
        if inp.prefer == "experimental":
            raise
        af = await fetch_alphafold_structure(
            FetchAlphaFoldInput(
                uniprot_id=inp.uniprot_id,
                include_pdb_text=inp.include_pdb_text,
                max_pdb_kb=inp.max_pdb_kb,
            )
        )
        return FindBestStructureOutput(
            uniprot_id=inp.uniprot_id,
            source="predicted",
            reason=(
                f"SIFTS suggested experimental structure {top.pdb_id} but the PDB "
                f"fetch failed ({e}). Fell back to the AlphaFold prediction."
            ),
            experimental_candidates=candidates,
            alphafold_result=af,
            caveats=[
                f"The preferred experimental structure {top.pdb_id} could not be "
                "fetched. The prediction is a substitute and may differ from the "
                "experimental conformation."
            ],
        )

    coverage_pct = _format_pct(top.coverage)
    resolution_str = _format_resolution(top.resolution_angstrom)
    reason = (
        f"SIFTS top match for UniProt {inp.uniprot_id} is PDB {top.pdb_id} "
        f"({top.experimental_method or 'method unspecified'}, {resolution_str}, "
        f"covering {coverage_pct} of the UniProt sequence, chain {top.chain_id or '?'})."
    )

    caveats: list[str] = []
    if top.coverage is not None and top.coverage < 0.5:
        caveats.append(
            f"The chosen experimental structure covers only {coverage_pct} of the "
            "full UniProt sequence — large regions of the protein are not represented. "
            "Consider the AlphaFold prediction for full-length context."
        )
    if len(candidates) > 1:
        caveats.append(
            f"{len(candidates) - 1} alternative experimental structure(s) were "
            "considered; see experimental_candidates for the list."
        )

    return FindBestStructureOutput(
        uniprot_id=inp.uniprot_id,
        source="experimental",
        reason=reason,
        experimental_candidates=candidates,
        pdb_result=pdb,
        caveats=caveats,
    )
