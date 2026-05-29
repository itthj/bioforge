"""Direct ClinVar lookup via NCBI E-utilities.

Complements `annotate_variant`. Both can reach ClinVar; the difference matters:

  - `annotate_variant` gets ClinVar significance as a SIDE EFFECT of the
    Ensembl VEP join. Fast (one HTTP call), but lossy: Ensembl indexes
    ClinVar on a release cadence (typically every 2-3 months), so brand-new
    submissions don't show up, and the per-record submitter detail (review
    status, trait names, SCV/RCV counts) is squashed into a handful of terms.
  - `lookup_clinvar` (this tool) talks to NCBI directly. Two HTTP calls
    (esearch → esummary) but returns the full record: germline classification,
    review status (the FDA-recognized 4-star scale), trait/condition names,
    SCV submission count, all aliases, all assembly coordinates. Current as of
    NCBI's last index — typically <24h behind submissions.

When to call which:
  - Looking up a SINGLE variant's clinical picture in detail → `lookup_clinvar`.
  - Annotating MANY variants from a VCF in bulk → `annotate_variant` (1 call,
    Ensembl covers consequence + ClinVar summary).
  - Variant is too new for Ensembl's last release → `lookup_clinvar`.

Input forms accepted: a numeric ClinVar Variation ID ('17661'), a VCV
accession ('VCV000017661'), an RCV accession ('RCV000019229'), or a free-text
search term. Numeric / VCV go straight to esummary; RCV and free-text route
via esearch first.

NCBI usage policy: requests must carry `tool=BioForge` and `email=...` so
they can contact us if our traffic is misbehaving. `email` is sourced from
`BIOFORGE_ENTREZ_EMAIL`. Empty email is allowed (NCBI uses a default rate
limit) but emits a caveat in the response.
"""

from __future__ import annotations

import re
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, field_validator

from bioforge.config import settings
from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
CLINVAR_HUMAN_URL = "https://www.ncbi.nlm.nih.gov/clinvar/variation/"

QueryKind = Literal["uid", "vcv", "rcv", "free_text"]

_UID_RE = re.compile(r"^\d+$")
_VCV_RE = re.compile(r"^VCV\d+(\.\d+)?$", re.IGNORECASE)
_RCV_RE = re.compile(r"^RCV\d+(\.\d+)?$", re.IGNORECASE)
# Permissive — esearch tolerates a lot; reject only obvious garbage.
_FREE_TEXT_RE = re.compile(r"^[A-Za-z0-9 _.:>=+\-\[\]/(),]+$")


class LookupClinvarInput(ToolInput):
    query: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description=(
            "What to look up. Accepts: (a) a numeric ClinVar Variation ID (e.g. '17661'), "
            "(b) a VCV accession (e.g. 'VCV000017661' or 'VCV000017661.157'), "
            "(c) an RCV accession (e.g. 'RCV000019229'), "
            "(d) a free-text ClinVar search term (e.g. 'BRCA1 c.181T>G' or 'NM_007294.4:c.181T>G'). "
            "Numeric and VCV inputs skip esearch and go straight to esummary; "
            "RCV / free-text inputs route via esearch first."
        ),
    )
    max_records: int = Field(
        default=5,
        ge=1,
        le=20,
        description=(
            "Cap on how many ClinVar records to fetch when the query matches multiple. "
            "Free-text queries can return dozens of hits; this prevents context-window blowups. "
            "Records are returned in NCBI's default relevance order."
        ),
    )

    @field_validator("query")
    @classmethod
    def _validate_query(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("query is empty")
        if not _FREE_TEXT_RE.match(stripped):
            raise ValueError(f"query contains unexpected characters: {stripped!r}")
        return stripped


def _classify_query(q: str) -> QueryKind:
    if _UID_RE.match(q):
        return "uid"
    if _VCV_RE.match(q):
        return "vcv"
    if _RCV_RE.match(q):
        return "rcv"
    return "free_text"


# --- Output schema ------------------------------------------------------------------


class ClinicalSignificance(BaseModel):
    """One classification record on a ClinVar variant.

    ClinVar carries three orthogonal classifications:
      - germline (inherited variant interpretation)
      - clinical_impact (somatic / tumor-relevant interpretation)
      - oncogenicity (oncogenic vs benign for somatic context)

    Many variants only carry germline. Empty classifications surface as
    `description=None` so the agent can tell "we have no data" from "we
    have a record saying it's benign".
    """

    description: str | None = Field(
        default=None,
        description="Classification term — e.g. 'Pathogenic', 'Likely benign', 'Uncertain significance'. None if no submitters at this level.",
    )
    review_status: str | None = Field(
        default=None,
        description=(
            "ClinVar 4-star review status: 'reviewed by expert panel' (***), "
            "'criteria provided, multiple submitters, no conflicts' (**), "
            "'criteria provided, single submitter' (*), or 'no assertion criteria' (0★). "
            "Drives confidence in the classification."
        ),
    )
    last_evaluated: str | None = Field(default=None, description="Date string from NCBI (YYYY/MM/DD format).")
    trait_names: list[str] = Field(
        default_factory=list,
        description="Condition / disease names associated with this classification.",
    )


class GenomicLocation(BaseModel):
    assembly_name: str
    chr: str
    start: int
    stop: int
    status: str = Field(
        description="'current' for the latest assembly, 'previous' for legacy coordinates (e.g. GRCh37)."
    )


class ClinVarRecord(BaseModel):
    """One ClinVar variation record."""

    uid: str = Field(description="Numeric Variation ID (e.g. '17661').")
    accession: str = Field(description="VCV accession (e.g. 'VCV000017661').")
    accession_version: str = Field(description="Versioned VCV (e.g. 'VCV000017661.157').")
    title: str = Field(description="Human-readable variant title, e.g. 'NM_007294.4(BRCA1):c.181T>G (p.Cys61Gly)'.")
    variant_type: str | None = Field(
        default=None, description="e.g. 'single nucleotide variant', 'Deletion', 'Duplication'."
    )
    cdna_change: str | None = Field(default=None, description="HGVS coding change (e.g. 'c.181T>G').")
    protein_change: str | None = Field(default=None, description="Protein change (e.g. 'C61G').")
    canonical_spdi: str | None = Field(default=None, description="Canonical SPDI representation if known.")
    aliases: list[str] = Field(default_factory=list, description="Alternative HGVS / legacy designations.")
    genes: list[str] = Field(default_factory=list, description="Gene symbols this variation overlaps.")
    molecular_consequences: list[str] = Field(default_factory=list)
    locations: list[GenomicLocation] = Field(default_factory=list)
    germline: ClinicalSignificance = Field(default_factory=ClinicalSignificance)
    clinical_impact: ClinicalSignificance = Field(default_factory=ClinicalSignificance)
    oncogenicity: ClinicalSignificance = Field(default_factory=ClinicalSignificance)
    scv_count: int = Field(default=0, description="Number of submitter (SCV) records aggregated into this variation.")
    rcv_count: int = Field(default=0, description="Number of RCV records (one per trait per allele).")
    clinvar_url: str = Field(description="Canonical NCBI URL for the record.")


class LookupClinvarOutput(ToolOutput):
    query: str
    query_kind: QueryKind = Field(description="How the query was classified before dispatch.")
    records: list[ClinVarRecord] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


# --- HTTP helpers (factored for test patching) --------------------------------------


def _eutils_params(extra: dict[str, str]) -> dict[str, str]:
    """Common E-utilities params: tool name + email per NCBI policy."""
    base = {"tool": "BioForge", "email": settings.entrez_email or ""}
    base.update(extra)
    return base


async def _esearch_clinvar(term: str, retmax: int) -> list[str]:
    """Run esearch against ClinVar and return the UID list. Raises ToolError on failure."""
    params = _eutils_params({"db": "clinvar", "term": term, "retmode": "json", "retmax": str(retmax)})
    url = f"{EUTILS_BASE}/esearch.fcgi"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url, params=params)
        except httpx.HTTPError as e:
            raise ToolError(f"NCBI esearch unreachable: {type(e).__name__}: {e}.") from e
    if resp.status_code != 200:
        raise ToolError(f"NCBI esearch returned HTTP {resp.status_code} for {term!r}: {resp.text[:200]!r}")
    try:
        payload = resp.json()
    except ValueError as e:
        raise ToolError(f"NCBI esearch returned non-JSON: {resp.text[:200]!r}") from e
    res = payload.get("esearchresult", {})
    if "ERROR" in res:
        raise ToolError(f"NCBI esearch error for {term!r}: {res['ERROR']}")
    return list(res.get("idlist", []))


async def _esummary_clinvar(uids: list[str]) -> dict[str, Any]:
    """Run esummary for the given UIDs and return the raw `result` dict."""
    if not uids:
        return {}
    params = _eutils_params({"db": "clinvar", "id": ",".join(uids), "retmode": "json"})
    url = f"{EUTILS_BASE}/esummary.fcgi"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url, params=params)
        except httpx.HTTPError as e:
            raise ToolError(f"NCBI esummary unreachable: {type(e).__name__}: {e}.") from e
    if resp.status_code != 200:
        raise ToolError(f"NCBI esummary returned HTTP {resp.status_code} for {uids!r}: {resp.text[:200]!r}")
    try:
        payload = resp.json()
    except ValueError as e:
        raise ToolError(f"NCBI esummary returned non-JSON: {resp.text[:200]!r}") from e
    return payload.get("result", {}) or {}


# --- Mapping ------------------------------------------------------------------------


def _map_classification(raw: dict[str, Any]) -> ClinicalSignificance:
    """Map a single classification sub-object (germline / clinical_impact / oncogenicity)."""
    if not raw or not isinstance(raw, dict):
        return ClinicalSignificance()
    desc = raw.get("description") or None
    review = raw.get("review_status") or None
    last = raw.get("last_evaluated") or None
    # NCBI uses '1/01/01 00:00' as a sentinel for "never evaluated" — squash to None.
    if last == "1/01/01 00:00":
        last = None
    traits = []
    for t in raw.get("trait_set", []) or []:
        if isinstance(t, dict):
            name = t.get("trait_name")
            if name:
                traits.append(name)
    return ClinicalSignificance(description=desc, review_status=review, last_evaluated=last, trait_names=traits)


def _map_locations(raw_variation_set: list[Any]) -> list[GenomicLocation]:
    locations: list[GenomicLocation] = []
    for var in raw_variation_set or []:
        if not isinstance(var, dict):
            continue
        for loc in var.get("variation_loc", []) or []:
            if not isinstance(loc, dict):
                continue
            try:
                locations.append(
                    GenomicLocation(
                        assembly_name=loc.get("assembly_name", ""),
                        chr=loc.get("chr", ""),
                        start=int(loc.get("start") or 0),
                        stop=int(loc.get("stop") or 0),
                        status=loc.get("status", ""),
                    )
                )
            except (TypeError, ValueError):
                # Skip a location that has unparseable positions rather than crashing the whole record.
                continue
    return locations


def _first_variation(raw: dict[str, Any]) -> dict[str, Any]:
    """ClinVar `variation_set` is a list — usually 1-element. Return the first dict or {}."""
    vs = raw.get("variation_set", []) or []
    for v in vs:
        if isinstance(v, dict):
            return v
    return {}


def _map_record(raw: dict[str, Any]) -> ClinVarRecord:
    """Convert one esummary result entry into our typed record."""
    uid = str(raw.get("uid", ""))
    accession = raw.get("accession", "")
    first_var = _first_variation(raw)
    aliases = list(first_var.get("aliases", []) or [])
    submissions = raw.get("supporting_submissions", {}) or {}
    genes = [g.get("symbol", "") for g in (raw.get("genes", []) or []) if isinstance(g, dict) and g.get("symbol")]
    return ClinVarRecord(
        uid=uid,
        accession=accession,
        accession_version=raw.get("accession_version", accession),
        title=raw.get("title", ""),
        variant_type=first_var.get("variant_type") or raw.get("obj_type"),
        cdna_change=first_var.get("cdna_change") or None,
        protein_change=raw.get("protein_change") or None,
        canonical_spdi=first_var.get("canonical_spdi") or None,
        aliases=aliases,
        genes=genes,
        molecular_consequences=list(raw.get("molecular_consequence_list", []) or []),
        locations=_map_locations(raw.get("variation_set", []) or []),
        germline=_map_classification(raw.get("germline_classification", {}) or {}),
        clinical_impact=_map_classification(raw.get("clinical_impact_classification", {}) or {}),
        oncogenicity=_map_classification(raw.get("oncogenicity_classification", {}) or {}),
        scv_count=len(submissions.get("scv", []) or []),
        rcv_count=len(submissions.get("rcv", []) or []),
        clinvar_url=f"{CLINVAR_HUMAN_URL}{uid}/" if uid else "",
    )


_BASE_CAVEATS = [
    "ClinVar aggregates submissions from many labs; review_status indicates how curated the call is (★★★★ FDA-recognized, ★★★ expert panel, ★★ multiple submitters concordant, ★ single submitter, 0★ no criteria). Treat the classification with confidence proportional to the stars.",
    "Variant interpretation evolves as new evidence accumulates. `last_evaluated` is the most-recent expert review; older records may not reflect current clinical opinion.",
    "ClinVar's clinical_impact and oncogenicity classifications are sparsely populated and germline classifications dominate. An empty classification means 'no submitter at this level', not 'evaluated as benign'.",
]


# --- Tool ----------------------------------------------------------------------------


@register_tool(
    name="lookup_clinvar",
    description=(
        "Query NCBI ClinVar directly for a variant's clinical interpretation. "
        "Accepts a numeric Variation ID, VCV/RCV accession, or free-text term. "
        "Returns the germline classification (e.g. 'Pathogenic'), review status "
        "(0★-4★ scale), associated traits / conditions, supporting submission "
        "counts (SCV/RCV), all genomic locations (GRCh38 + GRCh37), molecular "
        "consequences, and the canonical ClinVar URL. Use when the user asks "
        "'what does ClinVar say about variant X?', when looking up a single "
        "high-stakes variant in detail, or when Ensembl's last release may "
        "not yet have indexed a brand-new submission. For bulk annotation of "
        "many variants, prefer `annotate_variant` (single VEP call covers "
        "ClinVar summary too)."
    ),
    input_model=LookupClinvarInput,
    output_model=LookupClinvarOutput,
    version="1.0.0",
    citations=[
        "Landrum MJ et al. (2018) ClinVar: improving access to variant interpretations and supporting evidence. Nucleic Acids Res 46:D1062-D1067 (ClinVar)",
        "Sayers EW (2010) A General Introduction to the E-utilities. NCBI Books NBK25497 (E-utilities query model)",
    ],
    cost_hint="moderate",
    destructive=False,
    tags=["variants", "annotation", "clinvar"],
    reference_data_keys=["ncbi_clinvar"],
)
async def lookup_clinvar(inp: LookupClinvarInput) -> LookupClinvarOutput:
    kind = _classify_query(inp.query)

    if kind == "uid":
        uids = [inp.query]
    elif kind == "vcv":
        # esearch tolerates VCV with or without version — strip the version for safety.
        bare = inp.query.split(".", 1)[0]
        uids = await _esearch_clinvar(f"{bare}[VCV]", inp.max_records)
    elif kind == "rcv":
        bare = inp.query.split(".", 1)[0]
        uids = await _esearch_clinvar(f"{bare}[RCV]", inp.max_records)
    else:  # free_text
        uids = await _esearch_clinvar(inp.query, inp.max_records)

    if not uids:
        caveats = list(_BASE_CAVEATS)
        caveats.append(
            f"ClinVar esearch returned no UIDs for query {inp.query!r} (kind={kind}). "
            "Try a different form: a numeric Variation ID, a VCV/RCV accession, or a "
            "narrower free-text term."
        )
        if not settings.entrez_email:
            caveats.append(
                "BIOFORGE_ENTREZ_EMAIL is unset — NCBI subjects unidentified clients to a "
                "shared low rate limit. Set the env var to identify your usage."
            )
        return LookupClinvarOutput(query=inp.query, query_kind=kind, records=[], caveats=caveats)

    result = await _esummary_clinvar(uids[: inp.max_records])
    records: list[ClinVarRecord] = []
    # esummary returns `result.uids` listing the order + `result.{uid}` for each entry.
    ordered_uids = list(result.get("uids", uids))
    for uid in ordered_uids:
        raw = result.get(uid)
        if isinstance(raw, dict):
            records.append(_map_record(raw))

    caveats = list(_BASE_CAVEATS)
    if not settings.entrez_email:
        caveats.append(
            "BIOFORGE_ENTREZ_EMAIL is unset — NCBI subjects unidentified clients to a "
            "shared low rate limit. Set the env var to identify your usage."
        )
    if len(uids) > inp.max_records:
        caveats.append(
            f"Query matched {len(uids)} UIDs; only the first {inp.max_records} were retrieved. "
            "Raise `max_records` (up to 20) or narrow the query for the rest."
        )

    return LookupClinvarOutput(query=inp.query, query_kind=kind, records=records, caveats=caveats)
