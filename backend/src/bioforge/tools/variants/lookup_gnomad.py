"""Direct gnomAD lookup via GraphQL.

gnomAD (Karczewski 2020) aggregates exome + genome sequencing from hundreds
of thousands of individuals — the authoritative population-allele-frequency
reference for clinical variant interpretation. Where dbSNP's MAFs are loosely
aggregated across many studies, gnomAD reports per-cohort AC/AN/AF with QC
filter flags and structured ancestry breakdowns.

Architectural notes:
  - gnomAD's public API is GraphQL, not REST — so the fetcher POSTs a JSON
    body with `{"query": "...", "variables": {...}}` rather than the usual
    GET pattern. Errors arrive in two channels: HTTP status, AND an `errors`
    array inside a 200-status body. Both surface as ToolError.
  - Variant identifier is `chrom-pos-ref-alt` in left-aligned VCF format —
    NOT HGVS, NOT rsid. Insertions use anchor-base notation: a 1-bp insertion
    of G after position P with reference T at P is `chrom-P-T-TG`. This
    differs from HGVS right-shifted positions, so callers must derive the
    gnomAD form from `annotate_variant`'s colocated_variants[].id or
    vcf_string field rather than from raw HGVS.

When to call:
  - User asks about per-population allele frequency for a known clinical
    variant (e.g. "is this BRCA1 variant enriched in Ashkenazi Jewish?").
  - User cares about variant call quality (filters / flags).
  - Need separate exome vs genome cohort numbers.

When NOT to call:
  - For loose population context, dbSNP's `global_mafs` via `lookup_dbsnp` is
    cheaper and adequate.
  - For VEP consequences, annotate_variant.
  - For clinical assertion (pathogenic / benign), lookup_clinvar.

v1.0.0 accepts the gnomAD `chrom-pos-ref-alt` form only. Looking up by rsid
is supported by gnomAD's API but deferred to v1.1.0; the canonical chain is
annotate_variant → lookup_gnomad with the variant_id from VEP's response.
"""

from __future__ import annotations

import re
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, field_validator

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

GNOMAD_API_URL = "https://gnomad.broadinstitute.org/api"
GNOMAD_VARIANT_URL = "https://gnomad.broadinstitute.org/variant"

# Variant ID format: "<chrom>-<pos>-<ref>-<alt>"; chrom is 1-22, X, Y, M/MT.
# ref/alt are ACGT only (no N, no *, no IUPAC codes — those signal an upstream
# pipeline mismatch that should be cleaned up before hitting gnomAD).
_VARIANT_ID_RE = re.compile(r"^(?:[1-9]|1[0-9]|2[0-2]|X|Y|M|MT)-[1-9]\d*-[ACGT]+-[ACGT]+$")

GnomadDataset = Literal["gnomad_r4", "gnomad_r3", "gnomad_r2_1"]
_REFERENCE_BY_DATASET: dict[str, str] = {
    "gnomad_r4": "GRCh38",
    "gnomad_r3": "GRCh38",
    "gnomad_r2_1": "GRCh37",
}

# GraphQL query — kept small + focused on the fields we expose. Aliasing
# avoided so the response shape mirrors the schema.
_VARIANT_QUERY = """query VariantInfo($variantId: String!, $datasetId: DatasetId!) {
  variant(variantId: $variantId, dataset: $datasetId) {
    variant_id
    reference_genome
    chrom
    pos
    ref
    alt
    rsids
    flags
    exome {
      ac
      an
      af
      filters
      populations { id ac an }
    }
    genome {
      ac
      an
      af
      filters
      populations { id ac an }
    }
  }
}"""


class LookupGnomadInput(ToolInput):
    variant_id: str = Field(
        ...,
        min_length=5,
        max_length=200,
        description=(
            "gnomAD variant identifier in chrom-pos-ref-alt form (left-aligned VCF). "
            "Examples: '17-43057062-T-TG' (BRCA1 c.5266dupC, anchor-base notation for "
            "the +G insertion), '11-5227002-T-A' (HBB sickle). Derive from "
            "`annotate_variant`'s vcf_string field or its colocated_variants[].id. "
            "v1.0.0 does NOT accept HGVS or rsid input — those are deferred to v1.1.0."
        ),
    )
    dataset: GnomadDataset = Field(
        default="gnomad_r4",
        description=(
            "gnomAD release. 'gnomad_r4' is the current GRCh38-aligned release with "
            "~730k exomes + ~76k genomes. 'gnomad_r3' is GRCh38 genomes-only. "
            "'gnomad_r2_1' is the older GRCh37 release — use only when working with "
            "legacy clinical pipelines pinned to GRCh37."
        ),
    )

    @field_validator("variant_id")
    @classmethod
    def _validate_variant_id(cls, v: str) -> str:
        stripped = v.strip()
        if not _VARIANT_ID_RE.match(stripped):
            raise ValueError(
                f"variant_id must be 'chrom-pos-ref-alt' with chrom in 1-22/X/Y/M/MT and ref/alt in [ACGT]+, got {v!r}"
            )
        return stripped


# --- Output schema ------------------------------------------------------------------


class PopulationCount(BaseModel):
    """Allele count + chromosome count for one population/sex stratum.

    `id` follows gnomAD's stratum naming: 3-letter ancestry codes (afr, amr,
    asj, eas, fin, mid, nfe, sas, ami, remaining) and optional sex suffixes
    (_XX, _XY). Plain 'XX' / 'XY' are aggregate sex counts across all ancestries.
    `af` is computed from ac/an rather than passed through — keeps zero counts
    visible (gnomAD omits af when an=0).
    """

    id: str
    ac: int = Field(description="Allele count (number of chromosomes carrying the alt allele).")
    an: int = Field(description="Total chromosome count assayed in this stratum.")
    af: float | None = Field(
        default=None,
        description="Allele frequency = ac/an. None when an=0 (stratum unsampled).",
    )


class CohortSummary(BaseModel):
    """One sequencing cohort's view of the variant (exome OR genome)."""

    ac: int = Field(description="Allele count across the entire cohort.")
    an: int = Field(description="Total chromosomes assayed (haploid count).")
    af: float | None = Field(description="Global cohort AF. None when an=0.")
    filters: list[str] = Field(
        default_factory=list,
        description=(
            "Variant call quality filters. Empty = PASS. Non-empty (e.g. ['AC0'], "
            "['RF'], ['InbreedingCoeff']) means the call did NOT meet QC thresholds "
            "and the AF/AC numbers should be discounted for clinical use."
        ),
    )
    populations: list[PopulationCount] = Field(
        default_factory=list,
        description="Per-stratum breakdown (ancestry codes + sex strata). See PopulationCount.id docstring.",
    )


class GnomadRecord(BaseModel):
    """One variant's gnomAD record across exome + genome cohorts."""

    variant_id: str = Field(description="Canonical gnomAD variant identifier echoed back by the API.")
    reference_genome: str = Field(
        description="Assembly the variant_id is anchored to (GRCh38 for r3/r4, GRCh37 for r2_1)."
    )
    chrom: str
    pos: int
    ref: str
    alt: str
    rsids: list[str] = Field(default_factory=list, description="Cross-referenced dbSNP rsids.")
    flags: list[str] = Field(
        default_factory=list,
        description=(
            "Variant-level annotation flags (e.g. 'lcr' for low-complexity region, "
            "'segdup' for segmental duplication). Different from cohort filters — "
            "flags describe the variant; filters describe the call quality."
        ),
    )
    exome: CohortSummary | None = Field(
        default=None, description="Exome cohort. None when the variant has no exome data."
    )
    genome: CohortSummary | None = Field(
        default=None, description="Genome cohort. None when the variant has no genome data."
    )
    gnomad_url: str = Field(description="Canonical browser URL for this variant.")


class LookupGnomadOutput(ToolOutput):
    variant_id: str = Field(description="The variant_id as submitted (echoes the input).")
    dataset: str = Field(description="The dataset that was queried.")
    record: GnomadRecord = Field(
        description="The gnomAD record. Always populated on success; 'not found' raises ToolError."
    )
    caveats: list[str] = Field(default_factory=list)


# --- HTTP helpers (factored for test patching) -------------------------------------


async def _fetch_gnomad(variant_id: str, dataset: str) -> dict[str, Any]:
    """POST a GraphQL query for one variant and return the `data.variant` sub-dict.

    Raises ToolError on every failure mode: network, non-200, non-JSON, GraphQL
    errors[], or null variant (not in dataset).
    """
    body = {
        "query": _VARIANT_QUERY,
        "variables": {"variantId": variant_id, "datasetId": dataset},
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "BioForge/0.0.1 (Phase 3)",
    }
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            resp = await client.post(GNOMAD_API_URL, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise ToolError(
                f"gnomAD GraphQL unreachable: {type(e).__name__}: {e}. "
                "Check network connectivity to https://gnomad.broadinstitute.org."
            ) from e

    if resp.status_code == 429:
        raise ToolError(
            "gnomAD rate-limited the request (HTTP 429). Wait a few seconds and retry. "
            "gnomAD's public endpoint has aggressive rate limits; consider self-hosting "
            "the gnomAD browser for high-volume use."
        )
    if resp.status_code != 200:
        raise ToolError(f"gnomAD GraphQL returned HTTP {resp.status_code} for {variant_id!r}: {resp.text[:300]!r}")

    try:
        payload = resp.json()
    except ValueError as e:
        raise ToolError(f"gnomAD GraphQL returned non-JSON body: {resp.text[:200]!r}") from e

    # GraphQL puts upstream errors in a `errors[]` array even when HTTP is 200.
    errors = payload.get("errors") or []
    if errors:
        # gnomAD's "Variant not found" arrives this way — treat as not-found, not server-fail.
        messages = [e.get("message", "") for e in errors if isinstance(e, dict)]
        joined = "; ".join(m for m in messages if m) or repr(errors)
        if any("not found" in m.lower() for m in messages):
            raise ToolError(
                f"gnomAD has no record for {variant_id!r} in this dataset. "
                f"The variant may be private to populations not sampled, ultra-rare, "
                f"or the variant_id may be in the wrong reference build (gnomad_r4/r3 "
                f"use GRCh38, gnomad_r2_1 uses GRCh37). Detail: {joined}"
            )
        raise ToolError(f"gnomAD GraphQL error for {variant_id!r}: {joined}")

    data = payload.get("data") or {}
    variant = data.get("variant")
    if not isinstance(variant, dict):
        raise ToolError(
            f"gnomAD returned null variant for {variant_id!r}. "
            "Check the variant_id is in chrom-pos-ref-alt left-aligned VCF form, "
            "matches the dataset's reference build, and is a single SNV or small indel."
        )
    return variant


# --- Mapping ------------------------------------------------------------------------


def _af_from_counts(ac: int, an: int) -> float | None:
    """Compute AF locally rather than trust the API's `af` field — handles an=0 cleanly."""
    if an <= 0:
        return None
    return ac / an


def _map_population(raw: dict[str, Any]) -> PopulationCount | None:
    if not isinstance(raw, dict):
        return None
    pid = raw.get("id")
    if not isinstance(pid, str) or not pid:
        return None
    try:
        ac = int(raw.get("ac") or 0)
        an = int(raw.get("an") or 0)
    except (TypeError, ValueError):
        return None
    return PopulationCount(id=pid, ac=ac, an=an, af=_af_from_counts(ac, an))


def _map_cohort(raw: dict[str, Any] | None) -> CohortSummary | None:
    if not isinstance(raw, dict):
        return None
    try:
        ac = int(raw.get("ac") or 0)
        an = int(raw.get("an") or 0)
    except (TypeError, ValueError):
        return None
    populations: list[PopulationCount] = []
    for p in raw.get("populations") or []:
        mapped = _map_population(p)
        if mapped is not None:
            populations.append(mapped)
    return CohortSummary(
        ac=ac,
        an=an,
        af=_af_from_counts(ac, an),
        filters=[str(f) for f in (raw.get("filters") or []) if isinstance(f, str)],
        populations=populations,
    )


def _map_record(raw: dict[str, Any], dataset: str) -> GnomadRecord:
    variant_id = str(raw.get("variant_id", ""))
    return GnomadRecord(
        variant_id=variant_id,
        reference_genome=str(raw.get("reference_genome") or _REFERENCE_BY_DATASET.get(dataset, "")),
        chrom=str(raw.get("chrom", "")),
        pos=int(raw.get("pos") or 0),
        ref=str(raw.get("ref", "")),
        alt=str(raw.get("alt", "")),
        rsids=[str(r) for r in (raw.get("rsids") or []) if isinstance(r, str)],
        flags=[str(f) for f in (raw.get("flags") or []) if isinstance(f, str)],
        exome=_map_cohort(raw.get("exome")),
        genome=_map_cohort(raw.get("genome")),
        gnomad_url=f"{GNOMAD_VARIANT_URL}/{variant_id}?dataset={dataset}" if variant_id else "",
    )


_BASE_CAVEATS = [
    "gnomAD reports observed-allele counts in a sequenced cohort, NOT a population-genetics-style estimate. AF = ac / an where an is the number of chromosomes assayed at this position (haploid count).",
    "Per-population AF can deviate from global AF by 10-100x for founder variants (e.g. BRCA1 c.5266dupC enrichment in Ashkenazi Jewish, HBB sickle in West African ancestry). Inspect `populations` for the breakdown — global AF can mask large ancestry-specific signal.",
    "`filters` reports the variant call quality. Empty = PASS = trustworthy. Non-empty (e.g. ['AC0'], ['RF'], ['InbreedingCoeff']) means the call FAILED a QC threshold — the AC/AN/AF numbers should be treated with caution and verified against the variant's raw data.",
    "Variants absent from gnomAD do NOT mean 'not seen anywhere' — they may be private to populations gnomAD does not sample (e.g. specific Indigenous, Pacific, or Sub-Saharan groups under-represented in current releases), or ultra-rare below detection.",
    "gnomad_r4 / gnomad_r3 are GRCh38-anchored; gnomad_r2_1 is GRCh37. Cross-build variant_ids will return 'not found' — convert positions explicitly via liftover before querying.",
]


# --- Tool ----------------------------------------------------------------------------


@register_tool(
    name="lookup_gnomad",
    description=(
        "Look up a single variant in gnomAD by chrom-pos-ref-alt identifier. "
        "Returns per-population exome + genome allele counts, AF, QC filter "
        "flags, structural annotations, and the canonical browser URL. Use "
        "when the user asks about precise per-ancestry allele frequencies "
        "(e.g. Ashkenazi-founder enrichment, sickle-allele frequency in "
        "African ancestry) or about variant call quality. For coarse global "
        "frequency context use `lookup_dbsnp`; for ClinVar interpretation use "
        "`lookup_clinvar`. v1.0.0 takes the gnomAD variant_id only — derive "
        "it from `annotate_variant`'s vcf_string or colocated_variants[].id."
    ),
    input_model=LookupGnomadInput,
    output_model=LookupGnomadOutput,
    version="1.0.0",
    citations=[
        "Karczewski KJ et al. (2020) The mutational constraint spectrum quantified from variation in 141,456 humans. Nature 581:434-443 (gnomAD)",
        "Chen S et al. (2024) A genomic mutational constraint map using variation in 76,156 human genomes. Nature 625:92-100 (gnomAD v4)",
    ],
    cost_hint="moderate",
    destructive=False,
    tags=["variants", "annotation", "gnomad", "frequency"],
    reference_data_keys=["gnomad"],
)
async def lookup_gnomad(inp: LookupGnomadInput) -> LookupGnomadOutput:
    raw = await _fetch_gnomad(inp.variant_id, inp.dataset)
    record = _map_record(raw, inp.dataset)

    caveats = list(_BASE_CAVEATS)
    if record.exome is not None and record.exome.filters:
        caveats.append(
            f"Exome cohort has non-empty filters {record.exome.filters!r} — the exome AC/AN/AF "
            "for this variant should be treated as flagged. See gnomAD docs for filter meanings."
        )
    if record.genome is not None and record.genome.filters:
        caveats.append(
            f"Genome cohort has non-empty filters {record.genome.filters!r} — the genome AC/AN/AF "
            "for this variant should be treated as flagged."
        )

    return LookupGnomadOutput(
        variant_id=inp.variant_id,
        dataset=inp.dataset,
        record=record,
        caveats=caveats,
    )
