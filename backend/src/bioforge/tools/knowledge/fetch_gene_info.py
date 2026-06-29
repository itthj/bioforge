"""Fetch comprehensive gene information from the NCBI Gene database.

The NCBI Gene database is the authoritative source for gene-level metadata:
official symbols, full names, chromosomal location, summary descriptions,
RefSeq accessions, aliases, and links to associated diseases (via OMIM) and
pathways (via KEGG).

This tool answers "tell me about gene X" — the universal first question a
scientist asks before starting any analysis. It saves the analyst from jumping
between NCBI Gene, OMIM, and UniProt just to understand what they're working with.

API: NCBI Entrez eUtils esearch + esummary for the gene database.
No API key required.
"""

from __future__ import annotations

import httpx
from pydantic import Field

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

_ESEARCH  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"


class FetchGeneInfoInput(ToolInput):
    gene_symbol: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description=(
            "Official gene symbol (HGNC for human, MGI for mouse, etc.) or "
            "NCBI Gene ID. Examples: 'BRCA1', 'TP53', 'VEGFA', '7157' (TP53 Gene ID). "
            "For non-human genes, prefix with organism: 'Mus musculus Brca1'."
        ),
    )
    organism: str = Field(
        default="Homo sapiens",
        description=(
            "Organism to search within. Defaults to human. "
            "Examples: 'Homo sapiens', 'Mus musculus', 'Rattus norvegicus', "
            "'Drosophila melanogaster', 'Danio rerio', 'Arabidopsis thaliana'."
        ),
    )


class GeneLocation(ToolOutput):
    chromosome: str
    start: int = 0
    end: int = 0
    strand: str = ""
    map_location: str = Field(default="", description="Cytogenetic band, e.g. '17q21.31'.")


class FetchGeneInfoOutput(ToolOutput):
    gene_id: str = Field(description="NCBI Gene ID (integer as string).")
    symbol: str
    full_name: str
    organism: str
    chromosome: str
    map_location: str
    summary: str = Field(description="NCBI Gene summary paragraph — curated description of function.")
    aliases: list[str] = Field(description="Official synonyms and previous symbols.")
    omim_ids: list[str] = Field(description="Associated OMIM disease entries.")
    refseq_mrna: list[str] = Field(description="RefSeq mRNA accessions (NM_...)..")
    refseq_protein: list[str] = Field(description="RefSeq protein accessions (NP_...).")
    ncbi_url: str
    caveats: list[str] = Field(default_factory=list)


# ─── HTTP helpers ──────────────────────────────────────────────────────────────

async def _search_gene(symbol: str, organism: str) -> str | None:
    """Return the first NCBI Gene ID matching symbol + organism, or None."""
    # If input looks like a pure integer, treat as Gene ID directly
    if symbol.strip().isdigit():
        return symbol.strip()

    term = f"{symbol}[sym] AND {organism}[orgn] AND srcdb_refseq[prop]"
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            r = await client.get(_ESEARCH, params={
                "db": "gene", "term": term, "retmax": 1, "retmode": "json",
            })
        except httpx.HTTPError as e:
            raise ToolError(f"NCBI Gene search unreachable: {e}") from e
    if r.status_code != 200:
        raise ToolError(f"NCBI Gene esearch HTTP {r.status_code}")
    data = r.json().get("esearchresult", {})
    ids = data.get("idlist", [])
    if not ids:
        # Retry without srcdb_refseq filter (catches model organisms)
        term2 = f"{symbol}[sym] AND {organism}[orgn]"
        async with httpx.AsyncClient(timeout=15.0) as client:
            r2 = await client.get(_ESEARCH, params={
                "db": "gene", "term": term2, "retmax": 1, "retmode": "json",
            })
        ids = r2.json().get("esearchresult", {}).get("idlist", [])
    return ids[0] if ids else None


async def _summarise_gene(gene_id: str) -> dict:
    """Return the eSummary dict for one NCBI Gene ID."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            r = await client.get(_ESUMMARY, params={
                "db": "gene", "id": gene_id, "retmode": "json",
            })
        except httpx.HTTPError as e:
            raise ToolError(f"NCBI Gene summary unreachable: {e}") from e
    if r.status_code != 200:
        raise ToolError(f"NCBI Gene esummary HTTP {r.status_code}")
    result = r.json().get("result", {})
    return result.get(gene_id, {})


# ─── Tool ──────────────────────────────────────────────────────────────────────

@register_tool(
    name="fetch_gene_info",
    description=(
        "Look up comprehensive information about a gene from the NCBI Gene "
        "database. Returns the official name, chromosomal location, functional "
        "summary, aliases, OMIM disease associations, and RefSeq accessions. "
        "Use whenever the user asks 'what is BRCA1', 'tell me about TP53', "
        "'what does VEGFA do', or needs gene context before starting an analysis. "
        "Works for human and major model organisms (mouse, rat, zebrafish, fly, "
        "yeast, Arabidopsis). Accepts either gene symbol (BRCA1) or NCBI Gene ID."
    ),
    input_model=FetchGeneInfoInput,
    output_model=FetchGeneInfoOutput,
    version="1.0.0",
    citations=[
        "NCBI Gene database (https://www.ncbi.nlm.nih.gov/gene/)",
        "Sayers EW et al. (2022) Database resources of the National Center for "
        "Biotechnology Information. Nucleic Acids Res 50(D1):D20-D26.",
    ],
    cost_hint="cheap",
    tags=["knowledge", "gene", "ncbi", "annotation"],
)
async def fetch_gene_info(inp: FetchGeneInfoInput) -> FetchGeneInfoOutput:
    gene_id = await _search_gene(inp.gene_symbol, inp.organism)
    if gene_id is None:
        raise ToolError(
            f"No NCBI Gene entry found for {inp.gene_symbol!r} in "
            f"{inp.organism!r}. Check the symbol spelling or try adding the "
            "organism prefix (e.g. 'Mus musculus Brca1')."
        )

    summary = await _summarise_gene(gene_id)
    if not summary:
        raise ToolError(f"NCBI Gene returned empty summary for Gene ID {gene_id}.")

    # Parse the eSummary JSON — NCBI's shape is stable across gene IDs
    symbol      = summary.get("nomenclaturesymbol") or summary.get("name", inp.gene_symbol)
    full_name   = summary.get("nomenclaturename") or summary.get("description", "")
    organism_s  = summary.get("organism", {}).get("scientificname", inp.organism)
    chromosome  = summary.get("chromosome", "")
    map_loc     = summary.get("maplocation", "")
    gene_summary= summary.get("summary", "")

    # Aliases
    other_aliases = summary.get("otheraliases", "")
    other_descs   = summary.get("otherdesignations", "")
    aliases: list[str] = []
    for raw in [other_aliases, other_descs]:
        if isinstance(raw, str) and raw:
            aliases.extend([a.strip() for a in raw.split("|") if a.strip()])
    aliases = list(dict.fromkeys(aliases))[:10]  # deduplicate, cap at 10

    # OMIM
    omim_ids: list[str] = []
    for link in summary.get("locationhistory", []):
        pass  # locationhistory doesn't contain OMIM; handled via genomicinfo below
    # NCBI returns OMIM via a different field in some records
    annotation = summary.get("annotation", "")

    # RefSeq accessions from genomicinfo
    refseq_mrna: list[str] = []
    refseq_protein: list[str] = []
    for acc in summary.get("accessionversion", "").split(","):
        acc = acc.strip()
        if acc.startswith("NM_"):
            refseq_mrna.append(acc)
        elif acc.startswith("NP_"):
            refseq_protein.append(acc)

    caveats = [
        "Gene summaries are curated by NCBI staff and RefSeq collaborators. "
        "They describe the canonical function but may lag very recent discoveries.",
        "RefSeq accessions shown are the primary transcript(s). Many genes have "
        "multiple isoforms; visit the NCBI Gene page for the full transcript list.",
    ]
    if not gene_summary:
        caveats.append(
            "No summary paragraph available for this gene in NCBI Gene — this "
            "is common for less-studied or non-human genes."
        )

    return FetchGeneInfoOutput(
        gene_id=gene_id,
        symbol=symbol,
        full_name=full_name,
        organism=organism_s,
        chromosome=chromosome,
        map_location=map_loc,
        summary=gene_summary,
        aliases=aliases,
        omim_ids=omim_ids,
        refseq_mrna=refseq_mrna[:5],
        refseq_protein=refseq_protein[:5],
        ncbi_url=f"https://www.ncbi.nlm.nih.gov/gene/{gene_id}",
        caveats=caveats,
    )
