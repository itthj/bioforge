"""Fetch disease-gene associations from the Open Targets Platform.

Open Targets is the most comprehensive open evidence platform linking genes
to diseases and phenotypes through a curated, scored, multi-evidence framework.
It aggregates: genetic associations (GWAS, rare variants), somatic mutations,
known drugs, differential expression, animal models, pathways, and text-mining.

Every association is scored 0-1 per evidence channel and an overall score is
computed. This gives a far richer picture than OMIM or ClinVar alone.

API: Open Targets GraphQL v25 (https://api.platform.opentargets.org/api/v4/graphql)
No API key required.
"""

from __future__ import annotations

import httpx
from pydantic import Field

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

_OT_GRAPHQL = "https://api.platform.opentargets.org/api/v4/graphql"


class OpenTargetsInput(ToolInput):
    gene_symbol: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description=(
            "Gene symbol to look up disease associations. Examples: 'BRCA1', "
            "'TP53', 'PCSK9', 'CFTR'. Open Targets uses Ensembl gene IDs "
            "internally; we resolve the symbol automatically."
        ),
    )
    max_diseases: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of disease associations to return, ranked by overall score.",
    )
    min_score: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum overall association score (0-1). Default 0.1 returns all "
            "associations with any evidence. Use 0.5+ for strong associations only."
        ),
    )


class DiseaseAssociation(ToolOutput):
    disease_id: str = Field(description="EFO/MONDO disease ID.")
    disease_name: str
    overall_score: float = Field(description="Combined association score (0-1).")
    genetic_association_score: float = Field(description="Evidence from GWAS and rare variant studies.")
    somatic_mutation_score: float = Field(description="Evidence from somatic mutation databases.")
    known_drug_score: float = Field(description="Evidence from clinical trials (drug→disease link).")
    affected_pathway_score: float = Field(description="Evidence from pathway analysis.")
    literature_score: float = Field(description="Evidence from text-mining of literature.")
    open_targets_url: str


class OpenTargetsOutput(ToolOutput):
    gene_symbol: str
    ensembl_id: str
    n_associations: int
    diseases: list[DiseaseAssociation]
    gene_url: str
    caveats: list[str] = Field(default_factory=list)


# ─── GraphQL queries ─────────────────────────────────────────────────────────────

_SEARCH_QUERY = """
query SearchTarget($query: String!) {
  search(queryString: $query, entityNames: ["target"], page: {index: 0, size: 1}) {
    hits {
      id
      entity
      name
    }
  }
}
"""

_ASSOCIATIONS_QUERY = """
query TargetDiseases($ensemblId: String!, $size: Int!) {
  target(ensemblId: $ensemblId) {
    id
    approvedSymbol
    associatedDiseases(page: {index: 0, size: $size}) {
      count
      rows {
        disease {
          id
          name
        }
        score
        datatypeScores {
          componentId
          score
        }
      }
    }
  }
}
"""


async def _graphql(query: str, variables: dict) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            r = await client.post(
                _OT_GRAPHQL,
                json={"query": query, "variables": variables},
                headers={"Content-Type": "application/json"},
            )
        except httpx.HTTPError as e:
            raise ToolError(f"Open Targets API unreachable: {e}") from e
    if r.status_code != 200:
        raise ToolError(f"Open Targets HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    if "errors" in data:
        raise ToolError(f"Open Targets GraphQL error: {data['errors'][0].get('message', str(data['errors']))}")
    return data.get("data", {})


# ─── Tool ─────────────────────────────────────────────────────────────────────────

@register_tool(
    name="open_targets",
    description=(
        "Look up evidence-based disease associations for a gene from the Open "
        "Targets Platform. Returns diseases linked to the gene ranked by a "
        "combined association score (0-1) with sub-scores for genetic evidence, "
        "somatic mutations, known drugs, pathway analysis, and literature. "
        "Use when the user asks 'what diseases is BRCA1 associated with', "
        "'what conditions does TP53 mutations cause', 'is PCSK9 a drug target "
        "for any disease', or to provide clinical context for any gene. "
        "Particularly valuable for understanding the therapeutic relevance of "
        "CRISPR targets or differentially expressed genes."
    ),
    input_model=OpenTargetsInput,
    output_model=OpenTargetsOutput,
    version="1.0.0",
    citations=[
        "Ochoa D et al. (2023) The next-generation Open Targets Platform: "
        "reimagined, redesigned and reloaded. Nucleic Acids Res 51(D1):D1353-D1359.",
        "Open Targets GraphQL API (https://api.platform.opentargets.org/api/v4/graphql)",
    ],
    cost_hint="cheap",
    tags=["knowledge", "disease", "clinical", "therapeutic", "opentargets"],
)
async def open_targets(inp: OpenTargetsInput) -> OpenTargetsOutput:
    # Step 1: Resolve gene symbol → Ensembl ID
    search_data = await _graphql(_SEARCH_QUERY, {"query": inp.gene_symbol})
    hits = search_data.get("search", {}).get("hits", [])
    if not hits:
        raise ToolError(
            f"Open Targets could not find a gene matching {inp.gene_symbol!r}. "
            "Check the gene symbol spelling."
        )
    ensembl_id = hits[0]["id"]
    resolved_symbol = hits[0].get("name", inp.gene_symbol)

    # Step 2: Fetch disease associations
    assoc_data = await _graphql(
        _ASSOCIATIONS_QUERY,
        {"ensemblId": ensembl_id, "size": inp.max_diseases * 2},  # fetch extra, filter by score
    )
    target = assoc_data.get("target", {})
    if not target:
        raise ToolError(f"Open Targets returned no data for Ensembl ID {ensembl_id}.")

    assoc_block = target.get("associatedDiseases", {})
    total_count = assoc_block.get("count", 0)
    rows = assoc_block.get("rows", [])

    # Parse associations
    diseases: list[DiseaseAssociation] = []
    for row in rows:
        score = row.get("score", 0.0)
        if score < inp.min_score:
            continue
        disease = row.get("disease", {})
        disease_id   = disease.get("id", "")
        disease_name = disease.get("name", "")

        # Parse sub-scores by datatype
        sub: dict[str, float] = {}
        for ds in row.get("datatypeScores", []):
            sub[ds.get("componentId", "")] = ds.get("score", 0.0)

        diseases.append(DiseaseAssociation(
            disease_id=disease_id,
            disease_name=disease_name,
            overall_score=round(score, 4),
            genetic_association_score=round(sub.get("genetic_association", 0.0), 4),
            somatic_mutation_score=round(sub.get("somatic_mutation", 0.0), 4),
            known_drug_score=round(sub.get("known_drug", 0.0), 4),
            affected_pathway_score=round(sub.get("affected_pathway", 0.0), 4),
            literature_score=round(sub.get("literature", 0.0), 4),
            open_targets_url=(
                f"https://platform.opentargets.org/evidence/{ensembl_id}/{disease_id}"
            ),
        ))
        if len(diseases) >= inp.max_diseases:
            break

    caveats = [
        "Open Targets association scores are not measures of causality — a high "
        "genetic association score means the gene is statistically linked to the "
        "disease in GWAS/rare variant studies, not that it is the causal driver.",
        "The 'known_drug_score' reflects whether drugs targeting this gene have "
        "been tested in clinical trials for this disease, not whether they are approved.",
    ]
    if total_count > inp.max_diseases:
        caveats.append(
            f"This gene has {total_count} total disease associations; showing "
            f"the top {len(diseases)} by overall score. Visit the Open Targets "
            f"page for the full list."
        )

    gene_url = f"https://platform.opentargets.org/target/{ensembl_id}"

    return OpenTargetsOutput(
        gene_symbol=resolved_symbol,
        ensembl_id=ensembl_id,
        n_associations=len(diseases),
        diseases=diseases,
        gene_url=gene_url,
        caveats=caveats,
    )
