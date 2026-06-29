"""Fetch GWAS Catalog associations for a gene or trait.

The NHGRI-EBI GWAS Catalog is the most comprehensive public repository of
genome-wide association study results, curating >6,000 studies and >450,000
associations. It is the definitive source for answering:
  - "What traits are associated with variants near gene X?"
  - "What variants in gene Y are associated with disease Z?"
  - "Find all GWAS hits for type 2 diabetes"

Critical for:
  - Understanding the population genetics context of a gene target
  - Providing context for variant interpretation
  - Identifying potential off-target phenotypes of drug targets
  - Africa-specific genomics: GWAS Catalog indexes studies from all populations

API: GWAS Catalog REST API v1.0 (https://www.ebi.ac.uk/gwas/rest/api)
No API key required.
"""

from __future__ import annotations

import httpx
from pydantic import Field

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

_GWAS_BASE = "https://www.ebi.ac.uk/gwas/rest/api"


class GwasCatalogInput(ToolInput):
    query: str = Field(
        ...,
        min_length=2,
        max_length=100,
        description=(
            "Gene symbol OR trait/disease name to search. "
            "Examples: 'BRCA1' (gene mode), 'type 2 diabetes' (trait mode), "
            "'APOE' (gene), 'body mass index' (trait), 'LDL cholesterol'."
        ),
    )
    query_type: str = Field(
        default="auto",
        description=(
            "How to interpret the query: 'gene' (search by gene symbol), "
            "'trait' (search by trait/disease name), or 'auto' (heuristic: "
            "if query looks like a gene symbol, use gene mode; otherwise trait)."
        ),
    )
    max_results: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of associations to return.",
    )
    p_value_threshold: float = Field(
        default=5e-8,
        description=(
            "Genome-wide significance threshold. Default 5×10⁻⁸ (standard). "
            "Use 1e-5 to include suggestive associations."
        ),
    )


class GwasAssociation(ToolOutput):
    rsid: str = Field(description="dbSNP rsID of the lead variant.")
    chromosome: str
    position: int
    risk_allele: str
    p_value: float
    odds_ratio: float | None = None
    beta: float | None = None
    trait: str = Field(description="Reported trait from the GWAS study.")
    efo_trait_id: str = Field(description="EFO ontology term ID for the trait.")
    mapped_genes: list[str] = Field(description="Genes mapped to this variant.")
    study_accession: str = Field(description="GWAS Catalog study ID, e.g. GCST000001.")
    first_author: str
    pub_year: str
    pubmed_id: str
    gwas_catalog_url: str


class GwasCatalogOutput(ToolOutput):
    query: str
    query_mode: str
    n_associations: int
    associations: list[GwasAssociation]
    catalog_url: str
    caveats: list[str] = Field(default_factory=list)


def _looks_like_gene(query: str) -> bool:
    """Heuristic: short all-caps alphanumeric strings are likely gene symbols."""
    q = query.strip()
    return len(q) <= 10 and q.replace("-", "").replace(".", "").isupper()


async def _search_by_gene(gene: str, max_results: int, p_threshold: float) -> list[dict]:
    """Return raw GWAS association records for a gene."""
    url = f"{_GWAS_BASE}/associations/search"
    params = {
        "q": f"mappedGenes:{gene}",
        "size": max_results * 2,  # fetch extra for p-value filtering
        "sort": "pvalue,asc",
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            r = await client.get(url, params=params)
        except httpx.HTTPError as e:
            raise ToolError(f"GWAS Catalog API unreachable: {e}") from e
    if r.status_code != 200:
        raise ToolError(f"GWAS Catalog HTTP {r.status_code}")
    data = r.json()
    return data.get("_embedded", {}).get("associations", [])


async def _search_by_trait(trait: str, max_results: int) -> list[dict]:
    """Return raw GWAS association records for a trait."""
    url = f"{_GWAS_BASE}/associations/search"
    params = {
        "q": f"traitName:{trait}",
        "size": max_results * 2,
        "sort": "pvalue,asc",
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            r = await client.get(url, params=params)
        except httpx.HTTPError as e:
            raise ToolError(f"GWAS Catalog API unreachable: {e}") from e
    if r.status_code != 200:
        raise ToolError(f"GWAS Catalog trait search HTTP {r.status_code}")
    data = r.json()
    return data.get("_embedded", {}).get("associations", [])


def _parse_associations(raw: list[dict], p_threshold: float, max_results: int) -> list[GwasAssociation]:
    out: list[GwasAssociation] = []
    for item in raw:
        if len(out) >= max_results:
            break

        pval = item.get("pvalue") or item.get("pValueMantissa", 1.0)
        pexp = item.get("pvalueExponent") or item.get("pValueExponent", 0)
        try:
            actual_p = float(pval) * (10 ** int(pexp)) if pexp else float(pval)
        except (TypeError, ValueError):
            actual_p = 1.0

        if actual_p > p_threshold:
            continue

        # SNP info
        snps = item.get("snps", {})
        rsid = ""
        chrom = ""
        pos = 0
        if isinstance(snps, dict):
            embedded_snps = snps.get("_embedded", {}).get("singleNucleotidePolymorphisms", [])
            if embedded_snps:
                rsid = embedded_snps[0].get("rsId", "")
        risk_alleles = item.get("riskAlleles", [])
        risk_allele = ""
        if risk_alleles:
            ra = risk_alleles[0]
            if isinstance(ra, dict):
                risk_allele = ra.get("riskAlleleName", "")
                # parse chrom/pos from the SNP link if available
                snp_link = ra.get("snp", {})
                if isinstance(snp_link, dict):
                    rsid = rsid or snp_link.get("rsId", "")

        # Loci for chromosome/position
        loci = item.get("loci", [])
        if loci and isinstance(loci[0], dict):
            strongest = loci[0].get("strongestRiskAlleles", [])
            if strongest and isinstance(strongest[0], dict):
                snp_data = strongest[0].get("snp", {})
                if isinstance(snp_data, dict):
                    locs = snp_data.get("locations", [])
                    if locs and isinstance(locs[0], dict):
                        chrom = str(locs[0].get("chromosomeName", ""))
                        pos   = int(locs[0].get("chromosomePosition", 0))

        # Mapped genes
        mapped_genes: list[str] = []
        for gene_link in item.get("genomicContexts", []):
            if isinstance(gene_link, dict):
                gene = gene_link.get("gene", {})
                if isinstance(gene, dict):
                    name = gene.get("geneName", "")
                    if name:
                        mapped_genes.append(name)

        # Study / trait
        study_links = item.get("study", {})
        study_acc = ""
        trait_name = ""
        efo_id = ""
        author = ""
        year = ""
        pmid = ""
        if isinstance(study_links, dict):
            study_acc = study_links.get("accessionId", "")
            diseaseTrait = study_links.get("diseaseTrait", {})
            if isinstance(diseaseTrait, dict):
                trait_name = diseaseTrait.get("trait", "")
            efo_traits = study_links.get("efoTraits", [])
            if efo_traits and isinstance(efo_traits[0], dict):
                efo_id = efo_traits[0].get("shortForm", "")
            # Publication
            pub = study_links.get("publicationInfo", {})
            if isinstance(pub, dict):
                author = pub.get("author", {}).get("fullname", "") if isinstance(pub.get("author"), dict) else ""
                year = str(pub.get("publicationDate", ""))[:4]
                pmid = pub.get("pubmedId", "")

        or_val = item.get("orPerCopyNum")
        beta_val = item.get("betaNum")

        out.append(GwasAssociation(
            rsid=rsid or "unknown",
            chromosome=chrom,
            position=pos,
            risk_allele=risk_allele,
            p_value=actual_p,
            odds_ratio=float(or_val) if or_val is not None else None,
            beta=float(beta_val) if beta_val is not None else None,
            trait=trait_name,
            efo_trait_id=efo_id,
            mapped_genes=list(set(mapped_genes))[:5],
            study_accession=study_acc,
            first_author=author,
            pub_year=year,
            pubmed_id=pmid,
            gwas_catalog_url=(
                f"https://www.ebi.ac.uk/gwas/studies/{study_acc}" if study_acc else
                "https://www.ebi.ac.uk/gwas/"
            ),
        ))

    return out


@register_tool(
    name="gwas_catalog",
    description=(
        "Search the NHGRI-EBI GWAS Catalog for genome-wide association study "
        "results. Can query by gene symbol (returns traits associated with "
        "variants near the gene) or by trait/disease name (returns significant "
        "variants). Returns p-value, risk allele, odds ratio/beta, and the "
        "originating study. Use when the user asks 'what traits are associated "
        "with variants near BRCA1', 'find GWAS hits for type 2 diabetes', "
        "'what is the population genetics evidence linking APOE to disease', "
        "or to provide GWAS context for a variant or gene. Filters to "
        "genome-wide significant hits (p < 5×10⁻⁸) by default."
    ),
    input_model=GwasCatalogInput,
    output_model=GwasCatalogOutput,
    version="1.0.0",
    citations=[
        "Sollis E et al. (2023) The NHGRI-EBI GWAS Catalog: knowledgebase and "
        "deposition resource. Nucleic Acids Res 51(D1):D977-D985.",
        "GWAS Catalog REST API (https://www.ebi.ac.uk/gwas/rest/api)",
    ],
    cost_hint="cheap",
    tags=["knowledge", "gwas", "genetics", "variants", "population"],
)
async def gwas_catalog(inp: GwasCatalogInput) -> GwasCatalogOutput:
    # Determine query mode
    if inp.query_type == "auto":
        mode = "gene" if _looks_like_gene(inp.query) else "trait"
    else:
        mode = inp.query_type

    if mode == "gene":
        raw = await _search_by_gene(inp.query, inp.max_results, inp.p_value_threshold)
        catalog_url = f"https://www.ebi.ac.uk/gwas/genes/{inp.query.upper()}"
    else:
        raw = await _search_by_trait(inp.query, inp.max_results)
        catalog_url = f"https://www.ebi.ac.uk/gwas/search?query={inp.query.replace(' ', '+')}"

    associations = _parse_associations(raw, inp.p_value_threshold, inp.max_results)

    caveats = [
        "GWAS associations reflect statistical correlations in specific study "
        "populations. Effect sizes (OR/beta) may differ in other populations, "
        "particularly between European-ancestry cohorts and African, Asian, or "
        "admixed populations.",
        "Lead variants from GWAS are not necessarily the causal variants — they "
        "may be in linkage disequilibrium with the true functional variant.",
    ]
    if not associations:
        caveats.append(
            f"No GWAS Catalog associations found at p < {inp.p_value_threshold:.0e} "
            f"for {inp.query!r}. Try a more lenient p_value_threshold (e.g. 1e-5) "
            "or check the query spelling."
        )

    return GwasCatalogOutput(
        query=inp.query,
        query_mode=mode,
        n_associations=len(associations),
        associations=associations,
        catalog_url=catalog_url,
        caveats=caveats,
    )
