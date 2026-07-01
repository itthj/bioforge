"""Look up Human Phenotype Ontology (HPO) terms associated with a gene.

Answers "what clinical phenotypes are associated with mutations in gene X" —
the standard first question in rare-disease / clinical genetics workup, and
the data underlying tools like Exomiser's phenotype-driven variant
prioritization.

**API correction (see HANDOFF §10):** the old HPO REST API
(`ontology.jax.org/api` / `hpo.jax.org/api/hpo`) is deprecated and returns
404. HPO's gene-phenotype associations now live in the Monarch Initiative's
knowledge graph, served by Monarch API v3:
`https://api-v3.monarchinitiative.org/v3/api` (Putman et al. 2024).

Two-call pattern:
  1. `_search_gene` — resolve a gene symbol (e.g. 'BRCA1') to its HGNC CURIE
     (e.g. 'HGNC:1100') via `/v3/api/search`. Skipped when the input is
     already a bare `HGNC:NNNN` CURIE.
  2. `_fetch_associations` — `/v3/api/entity/{hgnc_id}/biolink:GeneToPhenotypicFeatureAssociation`
     returns the gene's HPO term associations.

Defensive parsing: Monarch's Biolink-model associations have been observed
to represent the `object` (the HPO term) as either a bare CURIE string or a
nested `{id, name}` object depending on ingest source — this tool handles
both shapes rather than assuming one. It also distinguishes a genuinely
empty association list (the recognized response field is present but empty
— zero associations for this gene) from an unrecognized response envelope
(none of the expected fields are present at all — a signal the API shape
has changed) by raising `ToolError` only in the latter case. An earlier
version of this tool conflated the two and silently reported n_terms=0 for
what was actually a broken parse.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from pydantic import BaseModel, Field, field_validator

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

MONARCH_API_BASE = "https://api-v3.monarchinitiative.org/v3/api"

_HGNC_CURIE_RE = re.compile(r"^HGNC:\d+$", re.IGNORECASE)

# Candidate envelope keys, tried in order, for each endpoint's list-bearing field.
# Monarch's v3 API is Biolink/KGX-derived and its wrapper key names have shifted
# across releases; we don't hard-fail on a name we haven't seen before as long as
# ONE recognized key is present.
_SEARCH_LIST_KEYS = ["items", "results", "hits"]
_ASSOCIATION_LIST_KEYS = ["associations", "items"]


class HpoTerm(BaseModel):
    hpo_id: str = Field(description="HPO term CURIE, e.g. 'HP:0001250' (Seizure).")
    hpo_name: str = Field(description="Human-readable HPO term label.")
    frequency: str | None = Field(
        default=None,
        description="Frequency qualifier if the source annotated one (e.g. an HPO frequency term or a percentage string).",
    )
    onset: str | None = Field(default=None, description="Onset qualifier if annotated (e.g. 'Congenital onset').")
    evidence: str | None = Field(default=None, description="Evidence code/type if annotated.")
    publications: list[str] = Field(default_factory=list, description="Supporting publication IDs (e.g. PMIDs), if annotated.")


class HpoPhenotypeInput(ToolInput):
    gene: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description=(
            "Human gene symbol (e.g. 'BRCA1') or a bare HGNC CURIE (e.g. 'HGNC:1100'). "
            "A CURIE skips the search step. Human genes only — Monarch's HGNC-anchored "
            "gene-phenotype associations are a human-specific resource."
        ),
    )
    max_terms: int = Field(default=50, ge=1, le=500, description="Maximum number of HPO term associations to return.")

    @field_validator("gene")
    @classmethod
    def _strip(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("gene is empty after stripping whitespace")
        return stripped


class HpoPhenotypeOutput(ToolOutput):
    query: str
    hgnc_id: str = Field(description="Resolved HGNC CURIE used for the lookup.")
    gene_symbol: str | None = Field(default=None, description="Gene symbol resolved during search, when a search was performed.")
    n_terms: int
    terms: list[HpoTerm]
    monarch_url: str = Field(description="Link to the gene's phenotype associations on the Monarch website.")
    caveats: list[str] = Field(default_factory=list)


def _extract_list(payload: dict, candidate_keys: list[str], context: str) -> list[dict]:
    """Pull the list of records out of a Monarch response envelope.

    Tries each candidate key in order. A recognized key holding an empty list is a
    legitimate zero-result answer. NONE of the candidate keys being present is treated
    as an unrecognized response shape (probable API change) and raises ToolError rather
    than silently reporting zero results — see module docstring.
    """
    for key in candidate_keys:
        if key in payload:
            value = payload[key]
            if not isinstance(value, list):
                raise ToolError(
                    f"Monarch {context} response field {key!r} was not a list "
                    f"(got {type(value).__name__}). The API response shape may have changed."
                )
            return value
    raise ToolError(
        f"Monarch {context} response did not contain any of the expected fields "
        f"{candidate_keys} (got top-level keys: {sorted(payload.keys())}). "
        "The API response shape may have changed."
    )


async def _search_gene(gene_symbol: str) -> tuple[str, str]:
    """Resolve a gene symbol to (hgnc_id, resolved_symbol) via /v3/api/search.

    Factored out for test patching — never hits the network in the test suite.
    """
    url = f"{MONARCH_API_BASE}/search"
    params = {"q": gene_symbol, "category": "biolink:Gene", "limit": 10}
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            r = await client.get(url, params=params)
        except httpx.HTTPError as e:
            raise ToolError(f"Monarch search API unreachable: {e}") from e
    if r.status_code != 200:
        raise ToolError(f"Monarch search API HTTP {r.status_code} for gene {gene_symbol!r}.")
    try:
        payload = r.json()
    except ValueError as e:
        raise ToolError(f"Monarch search API returned non-JSON response: {e}") from e

    items = _extract_list(payload, _SEARCH_LIST_KEYS, "search")
    if not items:
        raise ToolError(
            f"No Monarch gene entity found for {gene_symbol!r}. Check the spelling, or pass "
            "an explicit HGNC CURIE (e.g. 'HGNC:1100') if you already have one."
        )

    # Prefer an HGNC-prefixed hit (human gene) with an exact (case-insensitive) symbol match;
    # fall back to the first HGNC hit; fall back to the first hit of any kind.
    hgnc_hits = [it for it in items if isinstance(it, dict) and str(it.get("id", "")).upper().startswith("HGNC:")]
    exact = [it for it in hgnc_hits if str(it.get("name", "") or it.get("symbol", "")).upper() == gene_symbol.upper()]
    chosen = (exact or hgnc_hits or items)[0]

    hgnc_id = chosen.get("id")
    if not hgnc_id or not isinstance(hgnc_id, str):
        raise ToolError(f"Monarch search result for {gene_symbol!r} is missing an 'id' field: {chosen!r}")
    resolved_symbol = chosen.get("name") or chosen.get("symbol") or gene_symbol
    return hgnc_id, str(resolved_symbol)


async def _fetch_associations(hgnc_id: str, limit: int) -> list[dict]:
    """GET the gene's GeneToPhenotypicFeatureAssociation list. Factored out for test patching."""
    url = f"{MONARCH_API_BASE}/entity/{hgnc_id}/biolink:GeneToPhenotypicFeatureAssociation"
    params = {"limit": limit}
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            r = await client.get(url, params=params)
        except httpx.HTTPError as e:
            raise ToolError(f"Monarch entity API unreachable: {e}") from e
    if r.status_code != 200:
        raise ToolError(f"Monarch entity API HTTP {r.status_code} for {hgnc_id!r}.")
    try:
        payload = r.json()
    except ValueError as e:
        raise ToolError(f"Monarch entity API returned non-JSON response: {e}") from e

    return _extract_list(payload, _ASSOCIATION_LIST_KEYS, "gene-phenotype association")


def _object_id_and_name(assoc: dict) -> tuple[str, str]:
    """Handle both observed shapes of the `object` field: bare CURIE string, or nested dict."""
    obj = assoc.get("object")
    if isinstance(obj, dict):
        obj_id = str(obj.get("id", "") or "")
        obj_name = str(obj.get("name") or obj.get("label") or assoc.get("object_label") or obj_id)
    else:
        obj_id = str(obj or "")
        obj_name = str(assoc.get("object_label") or obj_id)
    return obj_id, obj_name


def _publications_list(assoc: dict) -> list[str]:
    raw = assoc.get("publications") or []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for p in raw:
        if isinstance(p, dict):
            pid = p.get("id") or p.get("publication")
            if pid:
                out.append(str(pid))
        elif p:
            out.append(str(p))
    return out


def _parse_terms(records: list[dict]) -> list[HpoTerm]:
    terms: list[HpoTerm] = []
    for assoc in records:
        if not isinstance(assoc, dict):
            continue
        obj_id, obj_name = _object_id_and_name(assoc)
        if not obj_id:
            continue
        qualifiers: dict[str, Any] = assoc if isinstance(assoc, dict) else {}
        terms.append(
            HpoTerm(
                hpo_id=obj_id,
                hpo_name=obj_name,
                frequency=qualifiers.get("frequency_qualifier") or qualifiers.get("frequency"),
                onset=qualifiers.get("onset_qualifier") or qualifiers.get("onset"),
                evidence=qualifiers.get("has_evidence") or qualifiers.get("evidence"),
                publications=_publications_list(assoc),
            )
        )
    return terms


@register_tool(
    name="hpo_phenotype",
    description=(
        "Look up Human Phenotype Ontology (HPO) clinical phenotype terms associated with a "
        "gene, via the Monarch Initiative knowledge graph. Use when the user asks 'what "
        "phenotypes are associated with gene X', 'what clinical features does a mutation in "
        "gene X cause', or wants HPO terms for rare-disease / clinical genetics workup. "
        "Accepts a gene symbol (e.g. 'BRCA1') or a bare HGNC CURIE (e.g. 'HGNC:1100'); a "
        "CURIE skips the symbol-resolution search step. Human genes only."
    ),
    input_model=HpoPhenotypeInput,
    output_model=HpoPhenotypeOutput,
    version="1.0.0",
    citations=[
        "Köhler S et al. (2021) The Human Phenotype Ontology in 2021. Nucleic Acids Res "
        "49(D1):D1207-D1217.",
        "Putman TE et al. (2024) The Monarch Initiative in 2024: an analytic platform "
        "integrating phenotypes, genes and diseases across species. Nucleic Acids Res "
        "52(D1):D938-D949.",
        "Monarch API v3 (https://api-v3.monarchinitiative.org/v3/api)",
    ],
    cost_hint="cheap",
    tags=["knowledge", "hpo", "phenotype", "clinical", "genetics"],
)
async def hpo_phenotype(inp: HpoPhenotypeInput) -> HpoPhenotypeOutput:
    gene_symbol: str | None = None
    if _HGNC_CURIE_RE.match(inp.gene):
        hgnc_id = inp.gene.upper()
    else:
        hgnc_id, gene_symbol = await _search_gene(inp.gene)

    records = await _fetch_associations(hgnc_id, inp.max_terms)
    terms = _parse_terms(records)[: inp.max_terms]

    caveats = [
        "HPO gene-phenotype associations are curated from OMIM, Orphanet, and other source "
        "databases via the Monarch knowledge graph; coverage and granularity vary by disease "
        "and by how recently the source database was updated.",
        "frequency/onset/evidence fields are populated only when the underlying source "
        "annotated them — many associations carry only the term itself.",
    ]
    if not terms:
        caveats.append(
            f"No HPO phenotype associations were found for {hgnc_id!r}. This may mean the gene "
            "has no curated disease-phenotype link in Monarch's current knowledge graph, not "
            "that no such link exists in the literature."
        )

    return HpoPhenotypeOutput(
        query=inp.gene,
        hgnc_id=hgnc_id,
        gene_symbol=gene_symbol,
        n_terms=len(terms),
        terms=terms,
        monarch_url=f"https://monarchinitiative.org/{hgnc_id}",
        caveats=caveats,
    )
