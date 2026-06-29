"""Fetch comprehensive protein information from UniProt.

UniProt is THE authoritative protein database, covering >250 million
sequences and providing expert-curated functional annotation for >570,000
proteins in Swiss-Prot. For any CRISPR target, DE gene, or protein of
interest, UniProt provides:

  - Canonical function (Gene Ontology–backed prose description)
  - Subcellular location (nucleus, membrane, cytoplasm…)
  - Post-translational modifications (phosphorylation sites, glycosylation…)
  - Active site and binding site residues
  - Disease associations (UniProt-curated links to variant databases)
  - Protein families and domains
  - Reviewed (Swiss-Prot) vs unreviewed (TrEMBL) status

API: UniProt REST API v2 (https://rest.uniprot.org)
No API key required.
"""

from __future__ import annotations

import httpx
from pydantic import Field

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

_UNIPROT_BASE = "https://rest.uniprot.org/uniprotkb"


class FetchUniprotInput(ToolInput):
    query: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description=(
            "UniProt accession (e.g. 'P38398' for BRCA1_HUMAN), gene symbol "
            "(e.g. 'BRCA1'), or protein name (e.g. 'breast cancer type 1'). "
            "Accession lookups are exact; gene/name lookups return the best-reviewed match. "
            "Append organism for disambiguation: 'BRCA1 Homo sapiens'."
        ),
    )
    reviewed_only: bool = Field(
        default=True,
        description=(
            "If True (default), only return Swiss-Prot reviewed entries. "
            "Set to False to also include TrEMBL unreviewed entries — "
            "useful for non-model organism proteins."
        ),
    )


class PTMSite(ToolOutput):
    position: int | str
    modification: str
    evidence: str = ""


class ActiveSite(ToolOutput):
    position: int | str
    description: str


class UniprotDisease(ToolOutput):
    disease_name: str
    acronym: str = ""
    description: str = ""
    omim_id: str = ""


class FetchUniprotOutput(ToolOutput):
    accession: str
    entry_name: str = Field(description="UniProt entry name, e.g. BRCA1_HUMAN.")
    reviewed: bool = Field(description="True if Swiss-Prot (curated); False if TrEMBL (automated).")
    gene_symbol: str
    protein_name: str = Field(description="Full recommended protein name.")
    organism: str
    length: int = Field(description="Sequence length in amino acids.")
    mass_da: int = Field(description="Molecular mass in Daltons.")
    function: str = Field(description="Curated functional description from UniProt.")
    subcellular_locations: list[str]
    ptm_sites: list[PTMSite] = Field(description="Key post-translational modification sites.")
    active_sites: list[ActiveSite]
    domains: list[str] = Field(description="Protein family and domain annotations.")
    disease_associations: list[UniprotDisease]
    go_terms: dict[str, list[str]] = Field(
        description="GO annotations grouped by aspect: biological_process, molecular_function, cellular_component."
    )
    sequence_url: str
    fasta_url: str
    uniprot_url: str
    caveats: list[str] = Field(default_factory=list)


async def _search_uniprot(query: str, reviewed_only: bool) -> str | None:
    """Return the best UniProt accession for a query string."""
    # If it looks like an accession (6-10 alphanumeric chars with typical UniProt pattern)
    import re
    if re.match(r'^[A-Z][0-9][A-Z0-9]{3}[0-9](-[0-9]+)?$', query.strip(), re.IGNORECASE):
        return query.strip().upper()

    filter_str = "reviewed:true " if reviewed_only else ""
    search_query = f"({filter_str}gene:{query} OR protein_name:{query})"

    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            r = await client.get(
                f"{_UNIPROT_BASE}/search",
                params={
                    "query": search_query,
                    "format": "json",
                    "size": 1,
                    "fields": "accession,reviewed",
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as e:
            raise ToolError(f"UniProt search unreachable: {e}") from e

    if r.status_code != 200:
        raise ToolError(f"UniProt search HTTP {r.status_code}: {r.text[:200]}")

    data = r.json()
    results = data.get("results", [])
    if not results:
        return None
    return results[0].get("primaryAccession")


async def _fetch_entry(accession: str) -> dict:
    """Fetch a full UniProt entry by accession."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            r = await client.get(
                f"{_UNIPROT_BASE}/{accession}",
                params={"format": "json"},
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as e:
            raise ToolError(f"UniProt entry fetch unreachable: {e}") from e

    if r.status_code == 404:
        raise ToolError(f"UniProt accession {accession!r} not found.")
    if r.status_code != 200:
        raise ToolError(f"UniProt HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def _safe_text(obj: object, *keys: str, default: str = "") -> str:
    node = obj
    for k in keys:
        if not isinstance(node, dict):
            return default
        node = node.get(k, {})
    return (node or "").strip() if isinstance(node, str) else default


def _parse_entry(data: dict) -> FetchUniprotOutput:  # noqa: C901
    acc = data.get("primaryAccession", "")
    entry_name = data.get("uniProtkbId", "")
    reviewed = data.get("entryType", "") == "UniProtKB reviewed (Swiss-Prot)"

    # Gene name
    genes = data.get("genes", [])
    gene_symbol = ""
    if genes:
        gn = genes[0].get("geneName", {})
        gene_symbol = gn.get("value", "") if isinstance(gn, dict) else ""

    # Protein name
    prot_desc = data.get("proteinDescription", {})
    rec_name = prot_desc.get("recommendedName", {})
    protein_name = _safe_text(rec_name, "fullName", "value")
    if not protein_name:
        sub_names = prot_desc.get("submissionNames", [])
        if sub_names:
            protein_name = _safe_text(sub_names[0], "fullName", "value")

    # Organism
    organism = _safe_text(data.get("organism", {}), "scientificName")

    # Sequence
    seq = data.get("sequence", {})
    length = seq.get("length", 0)
    mass_da = seq.get("molWeight", 0)

    # Function (from comments)
    function_text = ""
    subcellular: list[str] = []
    ptm_list: list[PTMSite] = []

    for comment in data.get("comments", []):
        ctype = comment.get("commentType", "")
        if ctype == "FUNCTION":
            texts = comment.get("texts", [])
            if texts:
                function_text = texts[0].get("value", "")[:2000]
        elif ctype == "SUBCELLULAR LOCATION":
            for loc_entry in comment.get("subcellularLocations", []):
                loc = loc_entry.get("location", {})
                val = loc.get("value", "")
                if val and val not in subcellular:
                    subcellular.append(val)
        elif ctype == "PTM":
            texts = comment.get("texts", [])
            if texts:
                ptm_list.append(PTMSite(position="various", modification=texts[0].get("value", "")[:200]))

    # Features (active sites, binding sites, mods)
    active_sites: list[ActiveSite] = []
    domains: list[str] = []

    for feat in data.get("features", []):
        ftype = feat.get("type", "")
        location = feat.get("location", {})
        pos = location.get("start", {}).get("value", "") if isinstance(location.get("start"), dict) else ""
        description = feat.get("description", "")

        if ftype == "Active site":
            active_sites.append(ActiveSite(position=pos, description=description))
        elif ftype in ("Modified residue", "Lipidation", "Glycosylation"):
            ptm_list.append(PTMSite(position=pos, modification=f"{ftype}: {description}", evidence=ftype))
        elif ftype in ("Domain", "Region", "Motif"):
            if description and description not in domains:
                domains.append(f"{ftype}: {description}")

    # Disease associations
    disease_list: list[UniprotDisease] = []
    for comment in data.get("comments", []):
        if comment.get("commentType") == "DISEASE":
            dis = comment.get("disease", {})
            omim_ids = [
                x.get("id", "") for x in dis.get("dbReferences", [])
                if x.get("type") == "MIM"
            ]
            disease_list.append(UniprotDisease(
                disease_name=dis.get("diseaseId", ""),
                acronym=dis.get("acronym", ""),
                description=dis.get("description", "")[:300],
                omim_id=omim_ids[0] if omim_ids else "",
            ))

    # GO terms
    go_terms: dict[str, list[str]] = {
        "biological_process": [],
        "molecular_function": [],
        "cellular_component": [],
    }
    aspect_map = {"P": "biological_process", "F": "molecular_function", "C": "cellular_component"}
    for xref in data.get("uniProtKBCrossReferences", []):
        if xref.get("database") == "GO":
            props = {p.get("key"): p.get("value") for p in xref.get("properties", [])}
            aspect = aspect_map.get(props.get("GoAspect", ""), "")
            term = props.get("GoTerm", "")
            if aspect and term and term not in go_terms[aspect]:
                go_terms[aspect].append(term)

    # Cap GO lists for readability
    for key in go_terms:
        go_terms[key] = go_terms[key][:15]

    caveats = []
    if not reviewed:
        caveats.append(
            "This is a TrEMBL (unreviewed) entry — annotations are computationally "
            "generated and have not been manually curated. Treat functional claims cautiously."
        )
    if not function_text:
        caveats.append(
            "No curated function description available. This is common for poorly-characterised "
            "or non-human proteins. Check the UniProt page for recent additions."
        )

    return FetchUniprotOutput(
        accession=acc,
        entry_name=entry_name,
        reviewed=reviewed,
        gene_symbol=gene_symbol,
        protein_name=protein_name,
        organism=organism,
        length=length,
        mass_da=mass_da,
        function=function_text,
        subcellular_locations=subcellular[:10],
        ptm_sites=ptm_list[:20],
        active_sites=active_sites[:10],
        domains=domains[:15],
        disease_associations=disease_list[:10],
        go_terms=go_terms,
        sequence_url=f"https://rest.uniprot.org/uniprotkb/{acc}.fasta",
        fasta_url=f"https://rest.uniprot.org/uniprotkb/{acc}.fasta",
        uniprot_url=f"https://www.uniprot.org/uniprotkb/{acc}/entry",
        caveats=caveats,
    )


@register_tool(
    name="fetch_uniprot",
    description=(
        "Fetch comprehensive protein information from UniProt for a gene symbol "
        "or UniProt accession. Returns the curated functional description, "
        "subcellular location, post-translational modifications, active/binding "
        "sites, disease associations, GO annotations, and protein family domains. "
        "Use when the user asks 'what does protein X do', 'where is BRCA1 "
        "localised', 'what are the phosphorylation sites on TP53', 'what diseases "
        "are caused by CFTR mutations', or to understand protein biology before "
        "or after a structural or CRISPR analysis. Accepts gene symbols (BRCA1), "
        "UniProt accessions (P38398), or protein names. Defaults to returning "
        "Swiss-Prot reviewed (curated) entries."
    ),
    input_model=FetchUniprotInput,
    output_model=FetchUniprotOutput,
    version="1.0.0",
    citations=[
        "The UniProt Consortium (2023) UniProt: the Universal Protein Knowledgebase "
        "in 2023. Nucleic Acids Res 51(D1):D523-D531.",
        "UniProt REST API v2 (https://rest.uniprot.org)",
    ],
    cost_hint="cheap",
    tags=["knowledge", "protein", "uniprot", "function", "annotation"],
)
async def fetch_uniprot(inp: FetchUniprotInput) -> FetchUniprotOutput:
    accession = await _search_uniprot(inp.query, inp.reviewed_only)
    if accession is None:
        if inp.reviewed_only:
            raise ToolError(
                f"No reviewed Swiss-Prot entry found for {inp.query!r}. "
                "Try reviewed_only=false to include unreviewed TrEMBL entries, "
                "or check the gene symbol spelling."
            )
        raise ToolError(
            f"No UniProt entry found for {inp.query!r}. "
            "Check the gene symbol, protein name, or accession."
        )

    raw = await _fetch_entry(accession)
    return _parse_entry(raw)
