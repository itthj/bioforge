"""Fetch drug-gene interactions from the Drug-Gene Interaction Database (DGIdb).

DGIdb aggregates drug-gene interaction data from 30+ curated sources including
DrugBank, ChEMBL, ClinicalTrials.gov, PharmGKB, and CIViC. It answers the
translational biologist's most important question: "Is there a drug that targets
this gene?" — bridging the gap between basic discovery and therapeutics.

Covers:
  - FDA-approved drugs with known gene targets
  - Investigational/clinical-trial drugs
  - Interaction type (inhibitor, activator, binder, etc.)
  - Source database for every interaction

API: DGIdb GraphQL v5 (https://dgidb.org/api/graphql)
No API key required.
"""

from __future__ import annotations

import httpx
from pydantic import Field

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

_DGIDB_GQL = "https://dgidb.org/api/graphql"


class DrugGeneInput(ToolInput):
    gene_symbol: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description=(
            "Gene symbol to look up drug interactions. Examples: 'EGFR', "
            "'BRCA1', 'BRAF', 'TP53', 'PCSK9', 'ACE2'. DGIdb normalises "
            "gene names automatically."
        ),
    )
    interaction_types: list[str] | None = Field(
        default=None,
        description=(
            "Optional filter for interaction type. Common values: 'inhibitor', "
            "'activator', 'binder', 'agonist', 'antagonist', 'antibody', "
            "'substrate'. Leave empty to return all types."
        ),
    )
    approved_only: bool = Field(
        default=False,
        description=(
            "If True, return only FDA-approved drugs. If False (default), "
            "include investigational and experimental compounds."
        ),
    )
    max_results: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of drug-gene interactions to return.",
    )


class DrugInteraction(ToolOutput):
    drug_name: str
    drug_concept_id: str = Field(description="DrugBank or ChEMBL concept ID if available.")
    interaction_type: str = Field(description="Type of interaction (inhibitor, activator, etc.).")
    approved: bool = Field(description="Whether this is an FDA-approved drug.")
    sources: list[str] = Field(description="Source databases reporting this interaction.")
    score: float = Field(description="DGIdb interaction score (higher = more sources agree).")


class DrugGeneOutput(ToolOutput):
    gene_symbol: str
    n_interactions: int
    n_approved_drugs: int
    interactions: list[DrugInteraction]
    dgidb_url: str
    caveats: list[str] = Field(default_factory=list)


_QUERY = """
query DrugInteractions($gene: String!) {
  genes(names: [$gene]) {
    nodes {
      name
      interactions {
        drug {
          name
          conceptId
          approved
        }
        interactionTypes {
          type
          directionality
        }
        interactionScore
        sources {
          sourceDbName
        }
      }
    }
  }
}
"""


async def _fetch_dgidb(gene_symbol: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            r = await client.post(
                _DGIDB_GQL,
                json={"query": _QUERY, "variables": {"gene": gene_symbol.upper()}},
                headers={"Content-Type": "application/json"},
            )
        except httpx.HTTPError as e:
            raise ToolError(f"DGIdb API unreachable: {e}") from e

    if r.status_code != 200:
        raise ToolError(f"DGIdb HTTP {r.status_code}: {r.text[:300]}")

    data = r.json()
    if "errors" in data:
        raise ToolError(f"DGIdb GraphQL error: {data['errors']}")

    nodes = data.get("data", {}).get("genes", {}).get("nodes", [])
    if not nodes:
        return []
    return nodes[0].get("interactions", [])


@register_tool(
    name="drug_gene_interaction",
    description=(
        "Look up drugs that interact with a gene from the Drug-Gene Interaction "
        "Database (DGIdb). Returns drug names, interaction types (inhibitor, "
        "activator, etc.), approval status, and source databases. Use when the "
        "user asks 'are there drugs that target EGFR', 'what inhibitors exist "
        "for BRAF', 'is BRCA1 druggable', 'what approved therapies target PCSK9', "
        "or to understand the therapeutic potential of a CRISPR target or DE gene. "
        "Covers 30+ curated sources including DrugBank, ChEMBL, and CIViC."
    ),
    input_model=DrugGeneInput,
    output_model=DrugGeneOutput,
    version="1.0.0",
    citations=[
        "Cannon M et al. (2024) DGIdb 5.0: rebuilding the drug-gene interaction "
        "database for precision medicine and drug discovery platforms. "
        "Nucleic Acids Res 52(D1):D1227-D1235.",
        "DGIdb GraphQL API v5 (https://dgidb.org/api/graphql)",
    ],
    cost_hint="cheap",
    tags=["knowledge", "drug", "therapeutic", "pharmacology", "dgidb"],
)
async def drug_gene_interaction(inp: DrugGeneInput) -> DrugGeneOutput:
    raw_interactions = await _fetch_dgidb(inp.gene_symbol)

    if not raw_interactions:
        return DrugGeneOutput(
            gene_symbol=inp.gene_symbol,
            n_interactions=0,
            n_approved_drugs=0,
            interactions=[],
            dgidb_url=f"https://dgidb.org/genes/{inp.gene_symbol.upper()}",
            caveats=[
                f"No drug-gene interactions found for {inp.gene_symbol!r} in DGIdb. "
                "This may mean (1) no drugs target this gene yet, (2) the gene "
                "symbol is non-standard, or (3) interactions exist in sources "
                "not yet integrated into DGIdb."
            ],
        )

    interactions: list[DrugInteraction] = []
    for item in raw_interactions:
        drug = item.get("drug", {})
        drug_name = drug.get("name", "Unknown")
        approved  = bool(drug.get("approved", False))

        if inp.approved_only and not approved:
            continue

        # Interaction types
        itype_list = item.get("interactionTypes", [])
        itype_str = ", ".join(
            t.get("type", "") for t in itype_list if t.get("type")
        ) or "unknown"

        if inp.interaction_types:
            if not any(
                it.lower() in itype_str.lower()
                for it in inp.interaction_types
            ):
                continue

        sources = [s.get("sourceDbName", "") for s in item.get("sources", []) if s.get("sourceDbName")]
        score   = float(item.get("interactionScore", 0.0))

        interactions.append(DrugInteraction(
            drug_name=drug_name,
            drug_concept_id=drug.get("conceptId", ""),
            interaction_type=itype_str,
            approved=approved,
            sources=list(set(sources))[:5],
            score=round(score, 3),
        ))

        if len(interactions) >= inp.max_results:
            break

    # Sort by score desc, then approved first
    interactions.sort(key=lambda x: (x.approved, x.score), reverse=True)

    n_approved = sum(1 for i in interactions if i.approved)

    caveats = [
        "DGIdb drug-gene interactions describe known pharmacological relationships; "
        "they do not imply clinical efficacy in any specific cancer, disease, or "
        "patient population.",
        "Interaction types (inhibitor, activator, etc.) are curated from source "
        "databases and may reflect different experimental contexts.",
    ]
    if inp.approved_only and len(interactions) == 0:
        caveats.append(
            "No FDA-approved drugs found. Try approved_only=false to include "
            "investigational compounds."
        )

    return DrugGeneOutput(
        gene_symbol=inp.gene_symbol.upper(),
        n_interactions=len(interactions),
        n_approved_drugs=n_approved,
        interactions=interactions,
        dgidb_url=f"https://dgidb.org/genes/{inp.gene_symbol.upper()}",
        caveats=caveats,
    )
