"""Fetch protein-protein interaction network from STRING.

STRING is the gold-standard database for protein interaction networks,
combining experimental evidence, co-expression, pathway databases, and
text-mining into a single confidence-scored network covering 59 million
proteins across 12,535 organisms.

Use cases:
  - "What proteins interact with BRCA1?" → understand the interaction landscape
  - "Find the top interactors of TP53" → identify therapeutic co-targets
  - Network context for a CRISPR target or DE gene
  - Identifying whether two proteins are in the same complex

API: STRING REST v11.5 at https://string-db.org/api/
No API key required for research use.
"""

from __future__ import annotations

import httpx
from pydantic import Field, field_validator

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

_STRING_BASE = "https://string-db.org/api/json"
_DEFAULT_SPECIES = 9606  # Homo sapiens NCBI taxon


_SPECIES_MAP = {
    "homo sapiens": 9606,
    "human": 9606,
    "mus musculus": 10090,
    "mouse": 10090,
    "rattus norvegicus": 10116,
    "rat": 10116,
    "danio rerio": 7955,
    "zebrafish": 7955,
    "drosophila melanogaster": 7227,
    "fly": 7227,
    "caenorhabditis elegans": 6239,
    "c. elegans": 6239,
    "saccharomyces cerevisiae": 4932,
    "yeast": 4932,
    "arabidopsis thaliana": 3702,
    "arabidopsis": 3702,
    "escherichia coli": 511145,
    "e. coli": 511145,
    "mycobacterium tuberculosis": 83332,
    "plasmodium falciparum": 36329,
}


class StringNetworkInput(ToolInput):
    protein: str = Field(
        ...,
        min_length=1,
        max_length=80,
        description=(
            "Protein name or gene symbol to query. Examples: 'BRCA1', 'TP53', "
            "'EGFR', 'p53'. STRING resolves common aliases automatically."
        ),
    )
    organism: str = Field(
        default="Homo sapiens",
        description=(
            "Organism. Accepts common names or scientific names. "
            "Examples: 'Homo sapiens', 'human', 'Mus musculus', 'mouse', "
            "'Saccharomyces cerevisiae', 'yeast'."
        ),
    )
    min_score: int = Field(
        default=400,
        ge=0,
        le=1000,
        description=(
            "Minimum STRING combined score (0-1000). "
            "400 = medium confidence (default), 700 = high confidence, "
            "900 = very high confidence. Lower = more interactors returned."
        ),
    )
    max_interactors: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of interaction partners to return.",
    )

    @property
    def species_id(self) -> int:
        return _SPECIES_MAP.get(self.organism.lower(), _DEFAULT_SPECIES)


class StringInteraction(ToolOutput):
    partner_name: str
    combined_score: int = Field(description="STRING combined confidence (0-1000).")
    experimental_score: int = Field(description="Experimental evidence sub-score.")
    coexpression_score: int = Field(description="Co-expression sub-score.")
    textmining_score: int = Field(description="Literature text-mining sub-score.")
    database_score: int = Field(description="Curated-database sub-score.")


class StringNetworkOutput(ToolOutput):
    query_protein: str
    string_id: str = Field(description="Resolved STRING protein ID.")
    organism: str
    n_interactors: int
    interactions: list[StringInteraction]
    string_url: str
    caveats: list[str] = Field(default_factory=list)


# ─── HTTP helpers ───────────────────────────────────────────────────────────────

async def _resolve_protein(protein: str, species_id: int) -> tuple[str, str] | None:
    """Return (string_id, preferred_name) or None if not found."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            r = await client.get(
                f"{_STRING_BASE}/resolve",
                params={"identifier": protein, "species": species_id, "limit": 1},
            )
        except httpx.HTTPError as e:
            raise ToolError(f"STRING API unreachable: {e}") from e
    if r.status_code == 200:
        results = r.json()
        if results:
            item = results[0]
            return item.get("stringId", ""), item.get("preferredName", protein)
    return None


async def _get_interactions(string_id: str, species_id: int, min_score: int, limit: int) -> list[dict]:
    """Return raw interaction records from STRING."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            r = await client.get(
                f"{_STRING_BASE}/interaction_partners",
                params={
                    "identifiers": string_id,
                    "species": species_id,
                    "required_score": min_score,
                    "limit": limit,
                    "caller_identity": "bioforge.tool",
                },
            )
        except httpx.HTTPError as e:
            raise ToolError(f"STRING interaction fetch failed: {e}") from e
    if r.status_code != 200:
        raise ToolError(f"STRING API HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


# ─── Tool ───────────────────────────────────────────────────────────────────────

@register_tool(
    name="string_network",
    description=(
        "Fetch the protein-protein interaction network for a protein from the "
        "STRING database. Returns the top interaction partners with confidence "
        "scores broken down by evidence type (experimental, co-expression, "
        "text-mining, curated databases). Use when the user asks 'what proteins "
        "interact with X', 'find binding partners of Y', 'what is the protein "
        "interaction network around Z', or to provide functional context after "
        "identifying a CRISPR target, DE gene, or variant-affected protein. "
        "Supports human and major model organisms."
    ),
    input_model=StringNetworkInput,
    output_model=StringNetworkOutput,
    version="1.0.0",
    citations=[
        "Szklarczyk D et al. (2023) The STRING database in 2023: protein-protein "
        "association networks and functional enrichment analyses for any of 12535 "
        "organisms. Nucleic Acids Res 51(D1):D638-D646.",
        "STRING REST API v11.5 (https://string-db.org/help/api/)",
    ],
    cost_hint="cheap",
    tags=["knowledge", "network", "protein", "interaction", "string"],
)
async def string_network(inp: StringNetworkInput) -> StringNetworkOutput:
    species_id = inp.species_id

    # Resolve protein name → STRING ID
    resolved = await _resolve_protein(inp.protein, species_id)
    if resolved is None:
        raise ToolError(
            f"STRING could not resolve {inp.protein!r} for species {inp.organism!r}. "
            "Check the protein name or try the human gene symbol."
        )
    string_id, preferred_name = resolved

    # Fetch interactions
    raw = await _get_interactions(string_id, species_id, inp.min_score, inp.max_interactors)

    interactions: list[StringInteraction] = []
    for item in raw:
        # STRING returns both directions; skip self-interactions
        partner = item.get("preferredName_B") or item.get("stringId_B", "")
        if not partner or partner.upper() == preferred_name.upper():
            partner = item.get("preferredName_A") or item.get("stringId_A", "")
        if not partner or partner.upper() == preferred_name.upper():
            continue
        interactions.append(StringInteraction(
            partner_name=partner,
            combined_score=int(item.get("score", 0) * 1000),
            experimental_score=int(item.get("escore", 0) * 1000),
            coexpression_score=int(item.get("coexpressionscore", 0) * 1000),
            textmining_score=int(item.get("tscore", 0) * 1000),
            database_score=int(item.get("dscore", 0) * 1000),
        ))

    # Sort by combined score descending
    interactions.sort(key=lambda x: x.combined_score, reverse=True)

    ncbi_id_part = string_id.split(".")[0] if "." in string_id else string_id
    string_url = f"https://string-db.org/network/{string_id}"

    caveats = [
        "STRING scores are probabilistic confidence estimates, not direct "
        "measures of interaction affinity or biological relevance. A high score "
        "means the interaction is well-supported by multiple evidence types, not "
        "that it is functionally important in your experimental context.",
        "The combined score aggregates all evidence channels; check the "
        "experimental sub-score (escore) if you need only biochemically validated "
        "interactions.",
    ]
    if len(interactions) == 0:
        caveats.append(
            f"No interactors found above the score threshold ({inp.min_score}). "
            "Try lowering min_score to 150 or 200 to see low-confidence interactions."
        )
    if len(interactions) == inp.max_interactors:
        caveats.append(
            f"Showing top {inp.max_interactors} interactors. Increase max_interactors "
            f"(up to 50) or visit {string_url} for the full network."
        )

    return StringNetworkOutput(
        query_protein=preferred_name,
        string_id=string_id,
        organism=inp.organism,
        n_interactors=len(interactions),
        interactions=interactions,
        string_url=string_url,
        caveats=caveats,
    )
