"""Direct dbSNP lookup via NCBI E-utilities.

Complements `annotate_variant` and `lookup_clinvar`:

  - `annotate_variant` returns dbSNP rsid cross-references as a side effect of
    the Ensembl VEP join (single HTTP call, bulk-friendly, but only the rsid
    string — no per-population allele frequencies, no functional class
    breakdown).
  - `lookup_dbsnp` (this tool) talks to NCBI directly. One HTTP call (esummary
    by numeric ID) returns the full dbSNP record: per-population allele
    frequencies across 1000 Genomes / gnomAD / TOPMED / ALFA / etc., gene
    overlap, GRCh38 + GRCh37 coordinates, SPDI representations, Sequence
    Ontology functional class, and dbSNP-aggregated clinical_significance tags.

When to call which:
  - User asks "what's the allele frequency of rs334 in 1000 Genomes?"
    → `lookup_dbsnp`. The per-population MAF breakdown is the headline.
  - User asks "what does ClinVar say about rs334?" → `lookup_clinvar`.
    dbSNP's clinical_significance tags are submitter-aggregated and looser
    than ClinVar's curated 4-star scale.
  - Bulk variant annotation → `annotate_variant`.

v1.0.0 accepts rsid input only ('rs334' or '334'). Coordinate input
(chrom, pos, ref, alt) is deferred to v1.1.0 — the agent should convert
position → rsid via `annotate_variant` (whose VEP response includes the
colocated rsid) and then call this tool.

NCBI usage policy: requests carry `tool=BioForge` and `email=...` from
`BIOFORGE_ENTREZ_EMAIL`. Empty email is allowed (shared low rate limit)
but emits a caveat in the response.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from pydantic import BaseModel, Field, field_validator

from bioforge.config import settings
from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
DBSNP_HUMAN_URL = "https://www.ncbi.nlm.nih.gov/snp/rs"

# rsid is "rs<digits>" or bare "<digits>".
_RSID_RE = re.compile(r"^(?:rs)?(\d+)$", re.IGNORECASE)

# Per-study freq encoding: "A=0.027356/137" → allele=A, freq=0.027356, count=137.
# Allele can also be multi-char (insertions) or a '-' (deletion marker).
_FREQ_TOKEN_RE = re.compile(r"^([A-Za-z\-]+)=([\d.eE+-]+)(?:/(\d+))?$")

# chrpos is "<chr>:<position>".
_CHRPOS_RE = re.compile(r"^([0-9A-Za-z._]+):(\d+)$")


class LookupDbsnpInput(ToolInput):
    query: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description=(
            "dbSNP rsid to look up. Accepts 'rs334' or the bare numeric ID '334' "
            "(case-insensitive). v1.0.0 supports rsid input only; coordinate input "
            "(chrom/pos/ref/alt) is not yet implemented — to look up by position, "
            "first call `annotate_variant` to get the colocated rsid, then call "
            "this tool with that rsid."
        ),
    )

    @field_validator("query")
    @classmethod
    def _validate_query(cls, v: str) -> str:
        stripped = v.strip()
        if not _RSID_RE.match(stripped):
            raise ValueError(f"query must be a dbSNP rsid like 'rs334' or '334', got {v!r}")
        return stripped


def _normalize_snp_id(query: str) -> str:
    """Strip optional 'rs' prefix, return bare digits. Validator guarantees match."""
    m = _RSID_RE.match(query.strip())
    assert m is not None, "validator should have rejected malformed query"
    return m.group(1)


# --- Output schema ------------------------------------------------------------------


class PopulationFrequency(BaseModel):
    """Allele frequency reported by one study/cohort.

    `study` is the dbSNP study handle (e.g. '1000Genomes', 'GnomAD_genomes',
    'TOPMED', 'ALFA'). `allele` is the allele being measured — NOT necessarily
    the minor allele; cross-check against the variant's reference allele.
    `sample_size` is the chromosome count (haploid), not individuals.
    """

    study: str = Field(description="dbSNP study handle, e.g. '1000Genomes', 'GnomAD_genomes', 'TOPMED', 'ALFA'.")
    allele: str = Field(
        description="Allele whose frequency is reported (e.g. 'A', 'T'). Not necessarily the minor allele."
    )
    frequency: float = Field(description="Allele frequency in [0.0, 1.0].")
    sample_size: int | None = Field(
        default=None,
        description="Chromosome count this frequency is derived from (haploid count, not individuals). None if not reported.",
    )


class GeneInfo(BaseModel):
    symbol: str = Field(description="Gene symbol (e.g. 'HBB').")
    gene_id: str | None = Field(default=None, description="NCBI Entrez Gene ID as string.")


class DbsnpRecord(BaseModel):
    """One dbSNP variant record."""

    rsid: str = Field(description="Canonical rsid (e.g. 'rs334').")
    snp_id: str = Field(description="Bare numeric ID (e.g. '334').")
    variant_class: str | None = Field(
        default=None,
        description="dbSNP variant class — e.g. 'snv' (single-nucleotide variant), 'del', 'ins', 'delins', 'mnv'.",
    )
    chromosome: str | None = Field(default=None, description="Chromosome name (e.g. '11', 'X', 'MT').")
    position_grch38: int | None = Field(default=None, description="1-based position on the current build (GRCh38).")
    position_grch37: int | None = Field(
        default=None,
        description="1-based position on the legacy build (GRCh37). Provided for clinical lab / pipeline compatibility.",
    )
    spdi: list[str] = Field(
        default_factory=list,
        description="Canonical SPDI representations (one per alt allele), e.g. ['NC_000011.10:5227001:T:A'].",
    )
    genes: list[GeneInfo] = Field(default_factory=list, description="Genes this variant overlaps.")
    functional_class: list[str] = Field(
        default_factory=list,
        description="Sequence Ontology consequence terms — e.g. ['missense_variant', 'coding_sequence_variant'].",
    )
    clinical_significance: list[str] = Field(
        default_factory=list,
        description=(
            "dbSNP-aggregated clinical significance tags — e.g. ['pathogenic', 'likely-benign', 'protective']. "
            "Looser than ClinVar's curated assertion; for clinical-grade interpretation use `lookup_clinvar`."
        ),
    )
    population_frequencies: list[PopulationFrequency] = Field(
        default_factory=list,
        description="Per-study allele frequencies. Population-specific MAFs can differ substantially.",
    )
    minor_allele: str | None = Field(
        default=None,
        description=(
            "Convenience pick: the non-reference allele with the highest non-zero frequency from a "
            "preferred-study list (1000Genomes → GnomAD_genomes → TOPMED → ALFA → first non-zero). "
            "Refer to `population_frequencies` for the full per-study breakdown."
        ),
    )
    minor_allele_frequency: float | None = Field(
        default=None,
        description="Frequency of `minor_allele` in the study used to pick it. Study-dependent — see caveats.",
    )
    minor_allele_source_study: str | None = Field(
        default=None,
        description="Which study `minor_allele_frequency` came from, so the agent can cite it.",
    )
    dbsnp_url: str = Field(description="Canonical NCBI URL for the record.")
    raw_docsum: str | None = Field(
        default=None,
        description="Raw NCBI docsum string (pipe-separated HGVS / protein change / sequence info). Provided unparsed because the format is brittle.",
    )


class LookupDbsnpOutput(ToolOutput):
    query: str = Field(description="The query as submitted (rsid form preserved).")
    record: DbsnpRecord = Field(
        description="The dbSNP record. Always populated on success — a 'not found' raises ToolError."
    )
    caveats: list[str] = Field(default_factory=list)


# --- HTTP / parse helpers -----------------------------------------------------------


def _eutils_params(extra: dict[str, str]) -> dict[str, str]:
    """Common E-utilities params: tool name + email per NCBI policy."""
    base = {"tool": "BioForge", "email": settings.entrez_email or ""}
    base.update(extra)
    return base


async def _fetch_dbsnp(snp_id: str) -> dict[str, Any]:
    """Run esummary against dbSNP for a single numeric ID.

    Returns the `result.{snp_id}` sub-dict. Raises ToolError on every failure
    mode (network, non-200, non-JSON, NCBI error field, missing entry).
    """
    params = _eutils_params({"db": "snp", "id": snp_id, "retmode": "json"})
    url = f"{EUTILS_BASE}/esummary.fcgi"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url, params=params)
        except httpx.HTTPError as e:
            raise ToolError(f"NCBI dbSNP esummary unreachable: {type(e).__name__}: {e}.") from e
    if resp.status_code != 200:
        raise ToolError(f"NCBI dbSNP esummary returned HTTP {resp.status_code} for rs{snp_id}: {resp.text[:200]!r}")
    try:
        payload = resp.json()
    except ValueError as e:
        raise ToolError(f"NCBI dbSNP esummary returned non-JSON: {resp.text[:200]!r}") from e
    result = payload.get("result", {}) or {}
    entry = result.get(snp_id)
    if not isinstance(entry, dict):
        raise ToolError(
            f"NCBI dbSNP returned no record for rs{snp_id}. Check the rsid is correct "
            f"and is a current (not withdrawn) accession; merged rsids should be looked "
            f"up at their current ID."
        )
    if entry.get("error"):
        raise ToolError(f"NCBI dbSNP error for rs{snp_id}: {entry['error']}")
    return entry


def _parse_freq_token(token: str) -> tuple[str, float, int | None] | None:
    """Parse 'A=0.027356/137' → (allele, freq, sample_size). Returns None on malformed.

    Some dbSNP tokens carry zero counts ('A=0./0') — treated as valid with frequency=0.
    """
    m = _FREQ_TOKEN_RE.match(token)
    if not m:
        return None
    allele, freq_str, count_str = m.groups()
    try:
        freq = float(freq_str)
    except ValueError:
        return None
    sample_size = int(count_str) if count_str is not None else None
    return allele, freq, sample_size


def _parse_chrpos(s: str | None) -> tuple[str | None, int | None]:
    if not s:
        return None, None
    m = _CHRPOS_RE.match(s)
    if not m:
        return None, None
    return m.group(1), int(m.group(2))


def _split_comma(s: str | None) -> list[str]:
    """Split a comma-joined NCBI string, drop empties / whitespace."""
    if not s:
        return []
    return [t.strip() for t in s.split(",") if t.strip()]


def _map_population_frequencies(raw_mafs: list[Any]) -> list[PopulationFrequency]:
    out: list[PopulationFrequency] = []
    for item in raw_mafs or []:
        if not isinstance(item, dict):
            continue
        study = item.get("study", "")
        token = item.get("freq", "")
        parsed = _parse_freq_token(token)
        if parsed is None:
            continue
        allele, freq, sample_size = parsed
        out.append(PopulationFrequency(study=study, allele=allele, frequency=freq, sample_size=sample_size))
    return out


_PREFERRED_STUDIES = ("1000Genomes", "GnomAD_genomes", "TOPMED", "ALFA")


def _pick_minor_allele(freqs: list[PopulationFrequency]) -> tuple[str | None, float | None, str | None]:
    """Pick a representative minor allele + frequency + source study.

    Preference order: 1000Genomes → GnomAD_genomes → TOPMED → ALFA → first
    non-zero study. Only non-zero frequencies are considered.
    """
    by_study: dict[str, PopulationFrequency] = {}
    for f in freqs:
        if f.frequency > 0 and f.study not in by_study:
            by_study[f.study] = f
    for study in _PREFERRED_STUDIES:
        if study in by_study:
            f = by_study[study]
            return f.allele, f.frequency, f.study
    if by_study:
        f = next(iter(by_study.values()))
        return f.allele, f.frequency, f.study
    return None, None, None


def _map_genes(raw: list[Any]) -> list[GeneInfo]:
    out: list[GeneInfo] = []
    for g in raw or []:
        if not isinstance(g, dict):
            continue
        symbol = g.get("name") or g.get("symbol")
        if not symbol:
            continue
        gene_id = g.get("gene_id")
        out.append(GeneInfo(symbol=symbol, gene_id=str(gene_id) if gene_id else None))
    return out


def _map_record(snp_id: str, raw: dict[str, Any]) -> DbsnpRecord:
    pop_freqs = _map_population_frequencies(raw.get("global_mafs", []) or [])
    minor_allele, minor_freq, minor_source = _pick_minor_allele(pop_freqs)
    chr38, pos38 = _parse_chrpos(raw.get("chrpos"))
    _, pos37 = _parse_chrpos(raw.get("chrpos_prev_assm"))
    return DbsnpRecord(
        rsid=f"rs{snp_id}",
        snp_id=snp_id,
        variant_class=raw.get("snp_class") or None,
        chromosome=chr38 or raw.get("chr") or None,
        position_grch38=pos38,
        position_grch37=pos37,
        spdi=_split_comma(raw.get("spdi")),
        genes=_map_genes(raw.get("genes", []) or []),
        functional_class=_split_comma(raw.get("fxn_class")),
        clinical_significance=_split_comma(raw.get("clinical_significance")),
        population_frequencies=pop_freqs,
        minor_allele=minor_allele,
        minor_allele_frequency=minor_freq,
        minor_allele_source_study=minor_source,
        dbsnp_url=f"{DBSNP_HUMAN_URL}{snp_id}",
        raw_docsum=raw.get("docsum") or None,
    )


_BASE_CAVEATS = [
    "dbSNP allele frequencies are study-specific. Frequencies in 1000 Genomes / gnomAD / TOPMED / ALFA / etc. can differ substantially by ancestry — a single global MAF can mask large population-specific differences. For clinical use, check the per-population breakdown in `population_frequencies`.",
    "`minor_allele` / `minor_allele_frequency` are a convenience pick from a preferred-study list (1000Genomes → GnomAD_genomes → TOPMED → ALFA → first non-zero). They are NOT a population-weighted global MAF — `minor_allele_source_study` says which study they came from. Treat as a quick orientation, not a clinical-grade frequency.",
    "dbSNP's `clinical_significance` tags are submitter-aggregated and looser than ClinVar's curated 4-star scale. For clinical interpretation, prefer `lookup_clinvar`.",
    "Coordinates differ between genome builds. `position_grch38` is current; `position_grch37` is provided for legacy lab and clinical-pipeline compatibility — do not mix the two.",
]


# --- Tool --------------------------------------------------------------------------


@register_tool(
    name="lookup_dbsnp",
    description=(
        "Look up a single dbSNP variant by rsid (e.g. 'rs334' or '334'). "
        "Returns per-population allele frequencies (1000 Genomes / gnomAD / "
        "TOPMED / ALFA / etc.), gene overlap, GRCh38 + GRCh37 coordinates, "
        "SPDI representations, Sequence Ontology functional class (e.g. "
        "missense_variant), dbSNP-aggregated clinical_significance tags, and "
        "a convenience minor-allele pick. Use when the user asks about allele "
        "frequencies, population genetics context, or the canonical dbSNP "
        "record for a variant. For ClinVar-grade clinical interpretation use "
        "`lookup_clinvar`. v1.0.0 accepts rsid input only — to look up by "
        "position, first call `annotate_variant` for the colocated rsid."
    ),
    input_model=LookupDbsnpInput,
    output_model=LookupDbsnpOutput,
    version="1.0.0",
    citations=[
        "Sherry ST et al. (2001) dbSNP: the NCBI database of genetic variation. Nucleic Acids Res 29:308-311 (dbSNP)",
        "Sayers EW (2010) A General Introduction to the E-utilities. NCBI Books NBK25497 (E-utilities query model)",
    ],
    cost_hint="moderate",
    destructive=False,
    tags=["variants", "annotation", "dbsnp"],
)
async def lookup_dbsnp(inp: LookupDbsnpInput) -> LookupDbsnpOutput:
    snp_id = _normalize_snp_id(inp.query)
    raw = await _fetch_dbsnp(snp_id)
    record = _map_record(snp_id, raw)

    caveats = list(_BASE_CAVEATS)
    if not settings.entrez_email:
        caveats.append(
            "BIOFORGE_ENTREZ_EMAIL is unset — NCBI subjects unidentified clients to a "
            "shared low rate limit. Set the env var to identify your usage."
        )

    return LookupDbsnpOutput(query=inp.query, record=record, caveats=caveats)
