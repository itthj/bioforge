"""Gene Ontology (GO) and pathway enrichment analysis via g:Profiler.

GO enrichment is required by every major journal for any paper that presents
a list of differentially-expressed, CRISPR screen hit, or otherwise derived
gene set. It answers: "Given these genes, what biological processes, molecular
functions, or cellular components are statistically over-represented?"

This tool uses the g:Profiler API (https://biit.cs.ut.ee/gprofiler/), which is:
  - Free, no API key required
  - Updated weekly with Ensembl, GO, KEGG, Reactome, WikiPathways, and TRANSFAC
  - The most widely used GO/pathway enrichment tool (>10,000 citations)
  - Returns statistically corrected p-values (g:SCS method, equivalent to BH FDR)

Supported databases (via the `sources` parameter):
  GO:BP  — Biological Process
  GO:MF  — Molecular Function
  GO:CC  — Cellular Component
  KEGG   — KEGG Pathways
  REAC   — Reactome Pathways
  WP     — WikiPathways
  TF     — TRANSFAC transcription factor binding sites
  HP     — Human Phenotype Ontology
"""

from __future__ import annotations

import httpx
from pydantic import Field, field_validator

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

_GPROFILER_URL = "https://biit.cs.ut.ee/gprofiler/api/gost/profile/"

_VALID_SOURCES = {"GO:BP", "GO:MF", "GO:CC", "KEGG", "REAC", "WP", "TF", "HP", "MIRNA"}
_SOURCE_NAMES = {
    "GO:BP": "GO Biological Process",
    "GO:MF": "GO Molecular Function",
    "GO:CC": "GO Cellular Component",
    "KEGG":  "KEGG Pathways",
    "REAC":  "Reactome Pathways",
    "WP":    "WikiPathways",
    "TF":    "Transcription Factor Binding (TRANSFAC)",
    "HP":    "Human Phenotype Ontology",
    "MIRNA": "miRNA targets (miRBase)",
}

_ORGANISMS = {
    "homo sapiens": "hsapiens",
    "human": "hsapiens",
    "mus musculus": "mmusculus",
    "mouse": "mmusculus",
    "rattus norvegicus": "rnorvegicus",
    "rat": "rnorvegicus",
    "danio rerio": "drerio",
    "zebrafish": "drerio",
    "drosophila melanogaster": "dmelanogaster",
    "fly": "dmelanogaster",
    "caenorhabditis elegans": "celegans",
    "c. elegans": "celegans",
    "saccharomyces cerevisiae": "scerevisiae",
    "yeast": "scerevisiae",
    "arabidopsis thaliana": "athaliana",
    "arabidopsis": "athaliana",
    "schizosaccharomyces pombe": "spombe",
}


class GoEnrichmentInput(ToolInput):
    genes: list[str] = Field(
        ...,
        min_length=1,
        max_length=2000,
        description=(
            "List of gene symbols or Ensembl IDs to test for enrichment. "
            "Minimum 2, maximum 2000. Examples: ['BRCA1', 'TP53', 'EGFR', 'VEGFA']. "
            "Works with HGNC symbols, Ensembl gene IDs (ENSG...), Entrez Gene IDs, "
            "UniProt accessions, and RefSeq accessions."
        ),
    )
    organism: str = Field(
        default="Homo sapiens",
        description="Organism. Supports 196 species via g:Profiler. Defaults to human.",
    )
    sources: list[str] = Field(
        default=["GO:BP", "GO:MF", "GO:CC", "KEGG", "REAC"],
        description=(
            "Annotation databases to query. Defaults to all GO namespaces plus "
            "KEGG and Reactome — the standard set for a publication. "
            "Options: GO:BP, GO:MF, GO:CC, KEGG, REAC, WP, TF, HP, MIRNA."
        ),
    )
    significance_threshold: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Adjusted p-value threshold (g:SCS corrected). Default 0.05.",
    )
    max_terms: int = Field(
        default=20,
        ge=1,
        le=200,
        description="Maximum number of enriched terms to return. Default 20.",
    )
    background_gene_count: int | None = Field(
        default=None,
        description=(
            "Optional: size of the statistical background (total genes in your "
            "experiment). If not provided, g:Profiler uses the full annotated "
            "genome as background — appropriate for most analyses."
        ),
    )

    @field_validator("sources")
    @classmethod
    def validate_sources(cls, v: list[str]) -> list[str]:
        invalid = set(v) - _VALID_SOURCES
        if invalid:
            raise ValueError(f"Unknown sources: {invalid}. Valid: {_VALID_SOURCES}")
        return v

    @field_validator("genes")
    @classmethod
    def validate_genes(cls, v: list[str]) -> list[str]:
        cleaned = [g.strip() for g in v if g.strip()]
        if len(cleaned) < 2:
            raise ValueError("At least 2 genes required for enrichment analysis.")
        return cleaned

    @property
    def organism_code(self) -> str:
        return _ORGANISMS.get(self.organism.lower(), "hsapiens")


class EnrichedTerm(ToolOutput):
    term_id: str = Field(description="GO/KEGG/Reactome term ID, e.g. GO:0007165.")
    term_name: str
    source: str = Field(description="Database source, e.g. GO:BP, KEGG, REAC.")
    p_value_adjusted: float = Field(description="g:SCS-corrected p-value.")
    n_genes_in_term: int = Field(description="Number of your query genes annotated to this term.")
    term_size: int = Field(description="Total genes annotated to this term in the genome.")
    genes_in_term: list[str] = Field(description="Which of your query genes hit this term.")


class GoEnrichmentOutput(ToolOutput):
    query_genes: list[str]
    n_genes_mapped: int = Field(description="Number of query genes successfully mapped to the annotation database.")
    n_significant_terms: int
    terms: list[EnrichedTerm]
    organism: str
    sources_queried: list[str]
    gprofiler_url: str = Field(description="Reproducible g:Profiler URL for this exact query.")
    caveats: list[str] = Field(default_factory=list)


# ─── HTTP helper ────────────────────────────────────────────────────────────────

async def _run_gprofiler(
    genes: list[str],
    organism: str,
    sources: list[str],
    sig_threshold: float,
    bg_count: int | None,
) -> dict:
    payload: dict = {
        "organism": organism,
        "query": genes,
        "sources": sources,
        "user_threshold": sig_threshold,
        "no_evidences": False,
        "no_iea": False,          # include electronically inferred annotations
        "measure_underrepresentation": False,
        "numeric_ns": "ENTREZGENE_ACC",
        "domain_scope": "annotated",
    }
    if bg_count is not None:
        payload["domain_scope"] = "custom_annotated"
        payload["background_enrich"] = bg_count

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            r = await client.post(_GPROFILER_URL, json=payload)
        except httpx.HTTPError as e:
            raise ToolError(f"g:Profiler API unreachable: {e}") from e

    if r.status_code != 200:
        raise ToolError(f"g:Profiler HTTP {r.status_code}: {r.text[:300]}")

    try:
        return r.json()
    except ValueError as e:
        raise ToolError(f"g:Profiler returned non-JSON response: {r.text[:200]}") from e


# ─── Parsing ────────────────────────────────────────────────────────────────────

def _parse_results(data: dict, genes: list[str], max_terms: int) -> tuple[list[EnrichedTerm], int]:
    result_block = data.get("result", [])
    if not result_block:
        return [], 0

    # g:Profiler wraps everything in result[0]
    meta   = result_block[0].get("meta", {}) if result_block else {}
    mapped_ids = meta.get("genes_metadata", {}).get("query", {})
    n_mapped   = len(mapped_ids) if isinstance(mapped_ids, dict) else len(genes)

    enrich_results = result_block[0].get("result", [])
    terms: list[EnrichedTerm] = []

    for item in enrich_results:
        if len(terms) >= max_terms:
            break
        term_id   = item.get("native", "")
        term_name = item.get("name", "")
        source    = item.get("source", "")
        p_adj     = item.get("p_value", 1.0)
        intersect = item.get("intersections", [])
        term_size = item.get("term_size", 0)
        n_in      = item.get("intersection_size", len(intersect))

        # intersections contains mapped IDs; convert back to readable if possible
        gene_hits: list[str] = []
        for entry in intersect:
            gene_hits.append(entry if isinstance(entry, str) else str(entry))

        terms.append(EnrichedTerm(
            term_id=term_id,
            term_name=term_name,
            source=source,
            p_value_adjusted=p_adj,
            n_genes_in_term=n_in,
            term_size=term_size,
            genes_in_term=gene_hits[:20],  # cap gene list per term
        ))

    return terms, n_mapped


# ─── Tool ───────────────────────────────────────────────────────────────────────

@register_tool(
    name="go_enrichment",
    description=(
        "Perform Gene Ontology (GO) and pathway enrichment analysis on a list "
        "of genes using g:Profiler. Returns statistically significant GO terms "
        "(Biological Process, Molecular Function, Cellular Component), KEGG "
        "pathways, and Reactome pathways with corrected p-values. Use whenever "
        "the user has a list of genes and asks 'what pathways are enriched', "
        "'what biological processes are these genes involved in', 'run GO "
        "analysis on these DEGs', or 'what do these genes have in common'. "
        "Essential for interpreting RNA-seq differential expression results, "
        "CRISPR screen hits, or any gene list. Requires at least 2 genes; "
        "works best with 20-500 genes."
    ),
    input_model=GoEnrichmentInput,
    output_model=GoEnrichmentOutput,
    version="1.0.0",
    citations=[
        "Kolberg L et al. (2023) g:Profiler — interoperable web service for "
        "functional enrichment analysis and gene identifier mapping. Nucleic "
        "Acids Res 51(W1):W207-W212.",
        "g:Profiler REST API (https://biit.cs.ut.ee/gprofiler/api/)",
    ],
    cost_hint="cheap",
    tags=["functional", "go", "pathway", "enrichment", "kegg", "reactome"],
)
async def go_enrichment(inp: GoEnrichmentInput) -> GoEnrichmentOutput:
    data = await _run_gprofiler(
        genes=inp.genes,
        organism=inp.organism_code,
        sources=inp.sources,
        sig_threshold=inp.significance_threshold,
        bg_count=inp.background_gene_count,
    )

    terms, n_mapped = _parse_results(data, inp.genes, inp.max_terms)

    caveats = [
        "P-values are corrected using the g:SCS method, which is equivalent to "
        "Benjamini-Hochberg FDR correction adapted for the dependence structure "
        "of the GO hierarchy. Terms with p_adjusted < 0.05 are considered "
        "significant under the conventional threshold.",
        "Very large gene lists (>500 genes) produce many significant terms — "
        "focus on low p-value terms with high n_genes_in_term relative to "
        "term_size (i.e. high recall) for the most interpretable results.",
    ]

    # Build g:Profiler URL for reproducibility
    gene_str = "%0A".join(inp.genes[:100])  # URL-encode newlines
    gprofiler_url = (
        f"https://biit.cs.ut.ee/gprofiler/gost?"
        f"organism={inp.organism_code}&query={gene_str}"
    )

    if not terms:
        caveats.append(
            "No significant enrichment found at the current threshold "
            f"(adjusted p < {inp.significance_threshold}). Consider: "
            "(1) checking gene symbol formatting, (2) lowering the threshold, "
            "(3) ensuring genes are from the correct organism."
        )

    source_labels = [_SOURCE_NAMES.get(s, s) for s in inp.sources]

    return GoEnrichmentOutput(
        query_genes=inp.genes,
        n_genes_mapped=n_mapped,
        n_significant_terms=len(terms),
        terms=terms,
        organism=inp.organism,
        sources_queried=source_labels,
        gprofiler_url=gprofiler_url,
        caveats=caveats,
    )
