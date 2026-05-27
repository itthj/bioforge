"""Fetch InterPro domain annotations for a UniProt accession.

InterPro is the EBI's consortium of protein signature databases (Pfam, SMART,
PROSITE, CATH-Gene3D, etc.). The InterPro entry catalog maps these signatures
to canonical, curated entries that describe protein domains, families,
homologous superfamilies, and binding/active sites.

This tool answers "what functional regions are in this protein?" — a critical
piece of structural interpretation. The frontend overlays the returned domain
ranges on the pLDDT bar (StructureCard) or chain pills (PdbStructureCard) so
the user immediately sees which regions of the protein are confidently
predicted AND functionally annotated.

API endpoint:
    https://www.ebi.ac.uk/interpro/api/entry/InterPro/protein/UniProt/{accession}/

Returns paginated JSON. We pull the first page (size 200) — InterPro entries
per protein rarely exceed 50, so one page covers everything in practice.

Cost hint: cheap. Single HTTP call, ~500 ms typically.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import Field

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

INTERPRO_API_BASE = "https://www.ebi.ac.uk/interpro/api/entry/InterPro/protein/UniProt"

# InterPro entry types we surface to the agent. The full set is broader but
# these are the biologically meaningful ones for a structural-context overlay.
_DOMAIN_TYPES = {
    "domain",
    "family",
    "homologous_superfamily",
    "repeat",
    "active_site",
    "binding_site",
    "conserved_site",
    "ptm",
}


class FetchInterproInput(ToolInput):
    uniprot_id: str = Field(
        ...,
        pattern=r"^[A-Z0-9]+$",
        min_length=6,
        max_length=10,
        description="UniProt accession (same shape as fetch_alphafold). Example: P38398 (BRCA1).",
    )
    max_domains: int = Field(
        default=50,
        ge=1,
        le=200,
        description=(
            "Maximum number of domain entries to return. Cap protects the agent's "
            "context window. 50 covers virtually every annotated protein."
        ),
    )


class DomainRegion(ToolOutput):
    """One contiguous residue range belonging to a domain entry. A single
    InterPro entry can have multiple regions on the same protein (repeats,
    discontinuous domains)."""

    start: int = Field(description="1-based start residue, inclusive.")
    end: int = Field(description="1-based end residue, inclusive.")


class InterproDomain(ToolOutput):
    interpro_id: str = Field(description="InterPro entry accession, e.g. IPR001357.")
    name: str
    type: str = Field(
        description="domain / family / homologous_superfamily / repeat / active_site / binding_site / conserved_site / ptm"
    )
    regions: list[DomainRegion]


class FetchInterproOutput(ToolOutput):
    uniprot_id: str
    num_entries: int
    domains: list[InterproDomain]
    caveats: list[str] = Field(default_factory=list)


# --- HTTP, factored out for test patching --------------------------------------------


async def _fetch_interpro(uniprot_id: str) -> list[dict[str, Any]]:
    """Fetch InterPro entries for a UniProt accession.

    Returns the `results` list from the InterPro response (or empty if 204/404).
    Patched in tests.
    """
    url = f"{INTERPRO_API_BASE}/{uniprot_id}/?page_size=200"
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
        except httpx.HTTPError as e:
            raise ToolError(f"InterPro API unreachable: {type(e).__name__}: {e}.") from e

    if resp.status_code in (204, 404):
        # 204/404 = "no entries for this UniProt". Biologically valid.
        return []
    if resp.status_code != 200:
        raise ToolError(f"InterPro API returned HTTP {resp.status_code} for {uniprot_id!r}: {resp.text[:200]!r}")

    try:
        payload = resp.json()
    except ValueError as e:
        raise ToolError(f"InterPro API returned non-JSON: {resp.text[:200]!r}") from e

    results = payload.get("results")
    if not isinstance(results, list):
        return []
    return results


# --- Parsing -------------------------------------------------------------------------


def _parse_entries(results: list[dict[str, Any]], uniprot_id: str) -> list[InterproDomain]:
    """Walk the InterPro response and pull out domains with their residue ranges.

    InterPro's shape: each result has `metadata` + `proteins` (list — usually
    one entry for the queried UniProt). Each protein has
    `entry_protein_locations`, which is a list of locations; each location has
    `fragments` with `start`/`end` pairs.
    """
    domains: list[InterproDomain] = []
    for entry in results:
        if not isinstance(entry, dict):
            continue
        meta = entry.get("metadata")
        if not isinstance(meta, dict):
            continue
        accession = meta.get("accession")
        if not isinstance(accession, str):
            continue
        entry_type = (meta.get("type") or "").lower()
        if entry_type and entry_type not in _DOMAIN_TYPES:
            continue
        name = meta.get("name") or accession

        proteins = entry.get("proteins") or []
        if not isinstance(proteins, list):
            continue

        regions: list[DomainRegion] = []
        for prot in proteins:
            if not isinstance(prot, dict):
                continue
            # Match the queried UniProt. InterPro responses for a UniProt query
            # generally only contain that protein, but be defensive.
            acc = prot.get("accession")
            if isinstance(acc, str) and acc.upper() != uniprot_id.upper():
                continue
            locations = prot.get("entry_protein_locations") or []
            for loc in locations:
                if not isinstance(loc, dict):
                    continue
                for frag in loc.get("fragments") or []:
                    if not isinstance(frag, dict):
                        continue
                    try:
                        start = int(frag.get("start"))
                        end = int(frag.get("end"))
                    except (TypeError, ValueError):
                        continue
                    if start <= 0 or end < start:
                        continue
                    regions.append(DomainRegion(start=start, end=end))

        if not regions:
            continue
        domains.append(
            InterproDomain(
                interpro_id=accession,
                name=name,
                type=entry_type or "domain",
                regions=regions,
            )
        )
    return domains


# --- Tool ----------------------------------------------------------------------------


@register_tool(
    name="fetch_interpro_domains",
    description=(
        "Fetch InterPro domain annotations for a UniProt protein. Returns a "
        "list of domains/families/active-sites with their residue ranges so "
        "the agent can identify functional regions of the protein and the "
        "frontend can overlay them on the structure card. Use whenever the "
        "user asks 'what domains does this protein have', 'where is the X "
        "domain in the sequence', or to add functional context to a structure "
        "answer."
    ),
    input_model=FetchInterproInput,
    output_model=FetchInterproOutput,
    version="1.0.0",
    citations=[
        "Paysan-Lafosse T et al. (2025) InterPro in 2025: a database of protein families, domains and functional sites. Nucleic Acids Res 53(D1):D444-D456",
        "EBI InterPro REST API (https://www.ebi.ac.uk/interpro/api/)",
    ],
    cost_hint="cheap",
    destructive=False,
    tags=["structure", "annotation", "interpro", "protein"],
)
async def fetch_interpro_domains(inp: FetchInterproInput) -> FetchInterproOutput:
    results = await _fetch_interpro(inp.uniprot_id)
    domains = _parse_entries(results, inp.uniprot_id)

    caveats: list[str] = [
        (
            "InterPro entries are predicted protein-signature matches, not "
            "experimentally validated. Use them as candidates for further "
            "investigation, not as ground truth."
        ),
        (
            "Different InterPro entries can describe overlapping or nested "
            "regions of the same protein (e.g. a 'family' entry covering most "
            "of a protein contains 'domain' entries inside it). Overlapping "
            "ranges are expected, not duplicates."
        ),
    ]

    if not domains:
        caveats.append(
            "No InterPro entries returned. Possible reasons: this UniProt entry "
            "has not been annotated, the accession is wrong, or the protein is "
            "too short/novel for any signature method to match."
        )

    truncated = False
    if len(domains) > inp.max_domains:
        domains = domains[: inp.max_domains]
        truncated = True
        caveats.append(
            f"Truncated to {inp.max_domains} entries — refine the query at "
            f"https://www.ebi.ac.uk/interpro/protein/UniProt/{inp.uniprot_id}/ "
            "to see the full annotation."
        )

    return FetchInterproOutput(
        uniprot_id=inp.uniprot_id,
        num_entries=len(domains) + (1 if truncated else 0),
        domains=domains,
        caveats=caveats,
    )
