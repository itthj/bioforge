"""Fetch CADD (Combined Annotation Dependent Depletion) pathogenicity scores.

CADD integrates dozens of annotations (conservation, regulatory, protein-level,
etc.) into a single deleteriousness score for any substitution in the human
genome, and is one of the most widely used variant-prioritization scores in
clinical and research genomics (ACMG guidelines cite it as supporting evidence
for PP3/BP4).

API: `https://cadd.gs.washington.edu/api/v1.0/<CADD-version>/<chrom>:<pos>_<ref>_<alt>`
(https://cadd.gs.washington.edu/api). No API key required. This is explicitly
described by the CADD team as an experimental, low-volume service — one
variant per call, not for batch scoring (use the downloadable pre-scored
tables for that).

Two API corrections baked into this implementation (see HANDOFF §10):
  1. There is no rsID lookup endpoint. Input must be `chrom:pos:ref:alt`.
     Chain with `lookup_dbsnp` first if you only have an rsID.
  2. The version string is NOT uniformly prefixed. v1.0-v1.3 are legacy,
     GRCh37-only, and take a BARE version string ("v1.3"). v1.4 onward
     require a genome-build prefix ("GRCh38-v1.7"). Valid build/version
     combinations are enumerated in `_VALID_COMBOS` below — not every
     version has both builds (v1.5 is GRCh38-only).
"""

from __future__ import annotations

import re

import httpx
from pydantic import Field, field_validator

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

CADD_API_BASE = "https://cadd.gs.washington.edu/api/v1.0"

# version -> set of genome builds CADD actually serves for it. Legacy versions
# (v1.0-v1.3) map to an empty set as a sentinel meaning "bare string, GRCh37 only,
# no build prefix in the URL" — handled specially in _compose_version_string.
_LEGACY_VERSIONS = {"v1.0", "v1.1", "v1.2", "v1.3"}
_VALID_COMBOS: dict[str, set[str]] = {
    "v1.4": {"GRCh37", "GRCh38"},
    "v1.5": {"GRCh38"},  # GRCh37-v1.5 was never released
    "v1.6": {"GRCh37", "GRCh38"},
    "v1.7": {"GRCh37", "GRCh38"},
}

_CHROM_RE = re.compile(r"^(?:chr)?([0-9XYMT]{1,2})$", re.IGNORECASE)
_BASE_RE = re.compile(r"^[ACGTacgt]+$")

_PHRED_BUCKETS: list[tuple[float, str]] = [
    (30.0, "top 0.1% most deleterious substitutions genome-wide"),
    (20.0, "top 1% most deleterious substitutions genome-wide"),
    (10.0, "top 10% most deleterious substitutions genome-wide"),
]


def _interpret_phred(phred: float) -> str:
    for threshold, label in _PHRED_BUCKETS:
        if phred >= threshold:
            return label
    return "not among the top 10% most deleterious substitutions genome-wide"


def _compose_version_string(cadd_version: str, genome_build: str) -> str:
    """Return the URL path segment CADD expects, or raise ToolError for an invalid combo."""
    if cadd_version in _LEGACY_VERSIONS:
        if genome_build != "GRCh37":
            raise ToolError(
                f"CADD {cadd_version} is a legacy release available for GRCh37 only "
                f"(no build prefix in the API). You requested genome_build={genome_build!r}. "
                f"Either set genome_build='GRCh37', or use cadd_version='v1.7' for GRCh38 scores."
            )
        return cadd_version
    builds = _VALID_COMBOS.get(cadd_version)
    if builds is None:
        raise ToolError(
            f"Unknown cadd_version {cadd_version!r}. Valid values: "
            f"{sorted(_LEGACY_VERSIONS)} (GRCh37 only, bare) or "
            f"{sorted(_VALID_COMBOS)} (paired with genome_build)."
        )
    if genome_build not in builds:
        raise ToolError(
            f"CADD {cadd_version} is not released for genome_build={genome_build!r}. "
            f"Available builds for {cadd_version}: {sorted(builds)}."
        )
    return f"{genome_build}-{cadd_version}"


class CaddScoreInput(ToolInput):
    chrom: str = Field(
        ...,
        description="Chromosome, e.g. '1', '17', 'X', 'MT'. A leading 'chr' is accepted and stripped.",
    )
    pos: int = Field(..., gt=0, description="1-based genomic position (matches the genome_build coordinate system).")
    ref: str = Field(..., min_length=1, max_length=200, description="Reference allele (e.g. 'A'). ACGT only.")
    alt: str = Field(..., min_length=1, max_length=200, description="Alternate allele (e.g. 'G'). ACGT only.")
    genome_build: str = Field(
        default="GRCh38",
        description="'GRCh38' (default) or 'GRCh37'. Must be available for the chosen cadd_version.",
    )
    cadd_version: str = Field(
        default="v1.7",
        description=(
            "CADD release. Recent: 'v1.7' (default, latest), 'v1.6', 'v1.5' (GRCh38 only), 'v1.4'. "
            "Legacy GRCh37-only releases: 'v1.0'-'v1.3' (require genome_build='GRCh37')."
        ),
    )

    @field_validator("chrom")
    @classmethod
    def _validate_chrom(cls, v: str) -> str:
        m = _CHROM_RE.match(v.strip())
        if not m:
            raise ValueError(f"chrom must look like '1'-'22', 'X', 'Y', or 'MT' (optionally 'chr'-prefixed); got {v!r}")
        return m.group(1).upper()

    @field_validator("ref", "alt")
    @classmethod
    def _validate_base(cls, v: str) -> str:
        stripped = v.strip()
        if not _BASE_RE.match(stripped):
            raise ValueError(f"allele must contain only A/C/G/T; got {v!r}")
        return stripped.upper()

    @field_validator("genome_build")
    @classmethod
    def _validate_build(cls, v: str) -> str:
        if v not in ("GRCh37", "GRCh38"):
            raise ValueError(f"genome_build must be 'GRCh37' or 'GRCh38'; got {v!r}")
        return v


class CaddScoreOutput(ToolOutput):
    chrom: str
    pos: int
    ref: str
    alt: str
    genome_build: str
    cadd_version: str
    raw_score: float = Field(description="Raw CADD score. Not directly comparable across CADD versions.")
    phred_score: float = Field(description="PHRED-scaled score: -10*log10(rank / total variants). Comparable across the genome.")
    interpretation: str = Field(description="Human-readable percentile bucket derived from phred_score.")
    caveats: list[str] = Field(default_factory=list)


async def _query_cadd(version_string: str, chrom: str, pos: int, ref: str, alt: str) -> list[dict]:
    """GET the single-SNV CADD endpoint. Factored out for test patching — never hits
    the network in the test suite. CADD queries can take 15-60s (server computes the
    score on the fly for novel variants not in the pre-scored table)."""
    url = f"{CADD_API_BASE}/{version_string}/{chrom}:{pos}_{ref}_{alt}"
    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            r = await client.get(url)
        except httpx.HTTPError as e:
            raise ToolError(
                f"CADD API unreachable: {e}. The service is experimental and occasionally "
                "unavailable; retry in a moment."
            ) from e
    if r.status_code != 200:
        raise ToolError(f"CADD API HTTP {r.status_code} for {chrom}:{pos}_{ref}_{alt} (version {version_string}).")
    try:
        data = r.json()
    except ValueError as e:
        raise ToolError(f"CADD API returned non-JSON response: {e}") from e
    if not isinstance(data, list):
        raise ToolError(f"CADD API returned an unexpected response shape (expected a list): {type(data).__name__}")
    return data


@register_tool(
    name="cadd_score",
    description=(
        "Fetch the CADD (Combined Annotation Dependent Depletion) deleteriousness score for a "
        "single-nucleotide substitution, given chrom:pos:ref:alt. Returns both the raw score and "
        "the PHRED-scaled score (comparable genome-wide; ACMG guidelines use CADD as PP3/BP4 "
        "supporting evidence). Use when the user asks 'how deleterious is this variant', 'what's "
        "the CADD score for chr17:43106487 T>G', or wants computational pathogenicity evidence for "
        "a variant. Input is chrom:pos:ref:alt ONLY — there is no rsID lookup endpoint; call "
        "lookup_dbsnp first if you only have an rsID and need the coordinates. The API is slow "
        "(15-60s per query, computed live for variants not in the pre-scored table) and is not "
        "meant for batch scoring."
    ),
    input_model=CaddScoreInput,
    output_model=CaddScoreOutput,
    version="1.0.0",
    citations=[
        "Rentzsch P et al. (2021) CADD-Splice-improving genome-wide variant effect prediction "
        "using deep learning-derived splice scores. Genome Med 13:31.",
        "Schubach M et al. (2024) CADD v1.7: using protein language models, regulatory CNNs and "
        "other nucleotide-level scores to improve genome-wide variant predictions. Nucleic Acids "
        "Res 52(D1):D1143-D1154.",
        "CADD API (https://cadd.gs.washington.edu/api)",
    ],
    cost_hint="moderate",
    tags=["variants", "pathogenicity", "cadd", "annotation"],
    published_accuracy={
        "phred_score": (
            "Rentzsch et al. 2021 / Schubach et al. 2024: PHRED-scaled rank among all possible "
            "genome-wide substitutions, not a probability of pathogenicity. Commonly used "
            "thresholds (PHRED >=20 top 1%, >=30 top 0.1%) are heuristic, not calibrated "
            "clinical cutoffs."
        ),
    },
)
async def cadd_score(inp: CaddScoreInput) -> CaddScoreOutput:
    version_string = _compose_version_string(inp.cadd_version, inp.genome_build)

    records = await _query_cadd(version_string, inp.chrom, inp.pos, inp.ref, inp.alt)

    if not records:
        raise ToolError(
            f"CADD returned no score for {inp.chrom}:{inp.pos} {inp.ref}>{inp.alt} "
            f"(version {version_string}). This usually means the ref/alt don't match the "
            f"reference genome at this position, or the position falls outside CADD's covered "
            f"regions. Double-check the ref allele against the {inp.genome_build} reference, "
            "or try a different genome_build."
        )

    # Prefer an exact Ref/Alt match if the endpoint ever returns more than one record;
    # otherwise take the first (the single-SNV endpoint should return exactly one).
    record = records[0]
    for r in records:
        if str(r.get("Ref", "")).upper() == inp.ref and str(r.get("Alt", "")).upper() == inp.alt:
            record = r
            break

    try:
        raw_score = float(record["RawScore"])
        phred_score = float(record["PHRED"])
    except (KeyError, TypeError, ValueError) as e:
        raise ToolError(
            f"CADD response is missing or has malformed score fields: {record!r} ({e})"
        ) from e

    caveats = [
        "CADD is a computational prediction, not a clinical determination. Per ACMG/AMP "
        "guidelines it may contribute supporting evidence (PP3/BP4), never a standalone "
        "pathogenic/benign call.",
        "raw_score is not comparable across CADD versions or genome builds; phred_score is "
        "the genome-wide-ranked value intended for cross-variant comparison.",
    ]
    if inp.cadd_version in _LEGACY_VERSIONS:
        caveats.append(
            f"{inp.cadd_version} is a legacy CADD release; consider cadd_version='v1.7' "
            "(current) for the most accurate score unless you specifically need version parity "
            "with older analyses."
        )

    return CaddScoreOutput(
        chrom=inp.chrom,
        pos=inp.pos,
        ref=inp.ref,
        alt=inp.alt,
        genome_build=inp.genome_build,
        cadd_version=inp.cadd_version,
        raw_score=raw_score,
        phred_score=phred_score,
        interpretation=_interpret_phred(phred_score),
        caveats=caveats,
    )
