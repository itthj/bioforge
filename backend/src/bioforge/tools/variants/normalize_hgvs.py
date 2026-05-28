"""Normalize an HGVS expression via Ensembl `variant_recoder`.

Where `format_hgvs` does pure-Python syntactic re-formatting (no network, no
biology lookup), `normalize_hgvs` calls Ensembl to apply the canonical HGVS
3'-shift rules and resolve a single input into ALL equivalent representations
across transcripts and reference sequences.

The textbook case is BRCA1 c.5266dupC — historically called "5382insC" in old
clinical-lab nomenclature because the duplicated C sits in a CCCC stretch and
could conceptually be expressed at multiple positions. Modern HGVS mandates
the 3'-most form, so both old strings normalize to the same genomic
`NC_000017.11:g.43057065dup` and coding `NM_007294.4:c.5266dup` outputs.

When to use:
  - Comparing variants between labs / databases where one might use the
    historic position and another the canonical one.
  - Re-expressing an input HGVS into the form a downstream tool needs (e.g.
    converting a coding-form input into the genomic form, or vice versa).
  - Resolving "which transcript family is this c.X form on?" by inspecting
    all returned hgvsc entries.

What the tool does NOT do:
  - Accept rsid input (variant_recoder supports it, but rsid users should
    start from `lookup_dbsnp`; deferred to v1.1.0).
  - Predict consequences — that's `annotate_variant`.
  - Look up clinical significance — that's `lookup_clinvar`.

Network cost: one HTTPS call to rest.ensembl.org. cost_hint=moderate.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field, field_validator

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

ENSEMBL_REST_BASE = "https://rest.ensembl.org"

# HGVS accepts a lot of forms; permissive validator, real check is what Ensembl returns.
_HGVS_RE = re.compile(r"^[A-Za-z0-9_.:>=+\-\[\]/]+$")


class NormalizeHgvsInput(ToolInput):
    hgvs: str = Field(
        ...,
        min_length=4,
        max_length=200,
        description=(
            "HGVS notation to normalize. Accepts transcript-level (e.g. "
            "'NM_007294.4:c.5266dupC'), Ensembl transcript ('ENST00000357654.9:c.5266dup'), "
            "or genomic ('17:g.43057065dup'). v1.0.0 is HGVS-only — rsid input "
            "is deferred to v1.1.0 (use `lookup_dbsnp` for rsid → record, then "
            "feed the returned HGVS here if normalization is still needed)."
        ),
    )
    species: str = Field(
        default="human",
        pattern=r"^[a-z_]+$",
        description="Ensembl species slug. Defaults to 'human'.",
    )
    max_transcript_forms: int = Field(
        default=20,
        ge=1,
        le=200,
        description=(
            "Cap on `hgvsc` / `hgvsp` list length per allele. BRCA1 has 200+ "
            "annotated transcripts, so a full dump bloats the agent's context. "
            "When the cap fires, `total_hgvsc_count` / `total_hgvsp_count` "
            "still reflect the un-capped totals and a caveat is emitted."
        ),
    )

    @field_validator("hgvs")
    @classmethod
    def _validate_hgvs(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("hgvs is empty")
        if not _HGVS_RE.match(stripped):
            raise ValueError(f"hgvs contains unexpected characters: {stripped!r}")
        return stripped


# --- Output schema ------------------------------------------------------------------


class NormalizedAllele(BaseModel):
    """One normalized allele form. variant_recoder returns one per alt allele;
    most inputs resolve to a single allele, but multi-allelic positions can
    return multiple entries.
    """

    allele: str = Field(description="The allele key under which Ensembl grouped this record (e.g. 'C', 'A').")
    input: str = Field(description="The input HGVS as Ensembl echoed it back (useful for sanity-checking).")
    primary_hgvsg: str | None = Field(
        default=None,
        description=(
            "Convenience: the first NC_* (RefSeq chromosome) genomic HGVS form, "
            "which is the canonical genomic representation most clinical pipelines expect."
        ),
    )
    primary_hgvsc: str | None = Field(
        default=None,
        description=(
            "Convenience: the first coding HGVS form. Falls back to `hgvsc[0]` "
            "if no transcript-family preference can be applied."
        ),
    )
    primary_hgvsp: str | None = Field(
        default=None,
        description="Convenience: the first protein HGVS form.",
    )
    hgvsg: list[str] = Field(
        default_factory=list,
        description="All genomic HGVS forms returned (e.g. NC_000017.11 + LRG_292 references).",
    )
    hgvsc: list[str] = Field(
        default_factory=list,
        description="Per-transcript coding HGVS forms. Capped to `max_transcript_forms` from input.",
    )
    hgvsp: list[str] = Field(
        default_factory=list,
        description="Per-protein HGVS forms. Capped to `max_transcript_forms`. NOT zipped with `hgvsc` by index — they're independent lists.",
    )
    spdi: list[str] = Field(
        default_factory=list,
        description="Canonical SPDI representations (sequence-position-deletion-insertion).",
    )
    total_hgvsc_count: int = Field(
        description="Un-capped count of returned hgvsc entries. If > `len(hgvsc)`, the list was trimmed."
    )
    total_hgvsp_count: int = Field(
        description="Un-capped count of returned hgvsp entries. If > `len(hgvsp)`, the list was trimmed."
    )


class NormalizeHgvsOutput(ToolOutput):
    query: str = Field(description="The input HGVS as the user submitted it.")
    alleles: list[NormalizedAllele] = Field(
        default_factory=list,
        description="Normalized forms per allele. Usually 1 entry; multi-allelic inputs return >1.",
    )
    caveats: list[str] = Field(default_factory=list)


# --- HTTP, factored for test patching ------------------------------------------------


async def _fetch_variant_recoder(hgvs: str, species: str) -> list[dict[str, Any]]:
    """Call Ensembl REST `/variant_recoder/{species}/{hgvs}` and return the JSON array.

    Ensembl returns a JSON array — one entry per submitted variant. We submit
    a single HGVS, so we always expect a one-element list of allele-keyed dicts.
    """
    url = f"{ENSEMBL_REST_BASE}/variant_recoder/{species}/{quote(hgvs, safe='')}"
    headers = {"Accept": "application/json", "User-Agent": "BioForge/0.0.1 (Phase 3)"}

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            resp = await client.get(url, headers=headers)
        except httpx.HTTPError as e:
            raise ToolError(
                f"Ensembl REST unreachable: {type(e).__name__}: {e}. "
                "Check network connectivity to https://rest.ensembl.org."
            ) from e

    if resp.status_code == 400:
        raise ToolError(
            f"Ensembl could not parse HGVS {hgvs!r} (HTTP 400). Check the input — "
            f"common issues: missing transcript version (use 'NM_007294.4', not 'NM_007294'), "
            f"non-canonical separator (must be ':'), or a coordinate that doesn't exist on "
            f"the named reference. Detail: {resp.text[:200]!r}"
        )
    if resp.status_code == 429:
        raise ToolError(
            "Ensembl REST rate-limited the request (HTTP 429). Wait a few seconds and retry; "
            "consider switching to a self-hosted VEP/variant_recoder instance for high-volume use."
        )
    if resp.status_code != 200:
        raise ToolError(f"Ensembl REST returned HTTP {resp.status_code} for {hgvs!r}: {resp.text[:300]!r}")

    try:
        payload = resp.json()
    except ValueError as e:
        raise ToolError(f"Ensembl REST returned non-JSON body: {resp.text[:200]!r}") from e

    if not isinstance(payload, list):
        raise ToolError(f"Ensembl REST returned non-list payload for {hgvs!r}: {type(payload).__name__}")
    return payload


# --- Mapping ------------------------------------------------------------------------


def _pick_primary_hgvsg(hgvsg: list[str]) -> str | None:
    """Pick the canonical genomic form: prefer NC_* (RefSeq chromosome) over LRG_* / other."""
    for s in hgvsg:
        if s.startswith("NC_"):
            return s
    return hgvsg[0] if hgvsg else None


def _map_allele(allele: str, raw: dict[str, Any], max_forms: int) -> NormalizedAllele:
    """Convert one allele sub-dict from the variant_recoder response."""
    raw_hgvsg = [s for s in (raw.get("hgvsg") or []) if isinstance(s, str)]
    raw_hgvsc = [s for s in (raw.get("hgvsc") or []) if isinstance(s, str)]
    raw_hgvsp = [s for s in (raw.get("hgvsp") or []) if isinstance(s, str)]
    raw_spdi = [s for s in (raw.get("spdi") or []) if isinstance(s, str)]
    return NormalizedAllele(
        allele=allele,
        input=raw.get("input", ""),
        primary_hgvsg=_pick_primary_hgvsg(raw_hgvsg),
        primary_hgvsc=raw_hgvsc[0] if raw_hgvsc else None,
        primary_hgvsp=raw_hgvsp[0] if raw_hgvsp else None,
        hgvsg=raw_hgvsg,  # always small (1-3 entries typically)
        hgvsc=raw_hgvsc[:max_forms],
        hgvsp=raw_hgvsp[:max_forms],
        spdi=raw_spdi,
        total_hgvsc_count=len(raw_hgvsc),
        total_hgvsp_count=len(raw_hgvsp),
    )


def _map_response(payload: list[dict[str, Any]], max_forms: int) -> list[NormalizedAllele]:
    """Each top-level dict is keyed by allele letter; flatten into a list of records."""
    out: list[NormalizedAllele] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        for allele, sub in entry.items():
            if not isinstance(sub, dict):
                continue
            # Skip reserved keys variant_recoder sometimes adds at the dict level (e.g.
            # 'warnings'). Allele keys are short alpha/dash strings; warnings is a list.
            if allele == "warnings":
                continue
            out.append(_map_allele(allele, sub, max_forms))
    return out


_BASE_CAVEATS = [
    "Ensembl variant_recoder applies HGVS 3'-shift (right-shift) normalization automatically — the returned `primary_hgvsg` is the canonical genomic form even when the user's input used a historic position (e.g. BRCA1 c.5266dupC vs the old 5382insC nomenclature).",
    "`hgvsc` and `hgvsp` are returned as independent lists, NOT zipped by index. The protein form for a specific transcript can't be derived by `hgvsp[i]` where `i` is the index of that transcript in `hgvsc` — use the transcript ID prefix (ENST/NM → ENSP/NP via standard cross-references) if you need the pairing.",
    "BRCA1, TP53, and other heavily-annotated genes can return 200+ transcript forms. Default `max_transcript_forms=20` keeps the output usable; check `total_hgvsc_count` / `total_hgvsp_count` to see whether trimming occurred.",
    "variant_recoder does NOT predict consequences or interpret clinical significance — chain `annotate_variant` and `lookup_clinvar` after for those.",
]


# --- Tool ----------------------------------------------------------------------------


@register_tool(
    name="normalize_hgvs",
    description=(
        "Normalize an HGVS expression via Ensembl variant_recoder. Returns the "
        "canonical right-shifted genomic form (`primary_hgvsg`) plus all equivalent "
        "transcript-level coding forms (`hgvsc`) and protein forms (`hgvsp`) and "
        "SPDI representations. Use when you need to: (a) re-express a variant in a "
        "different reference form (coding → genomic or vice versa), (b) reconcile "
        "the same biological variant submitted under different historic or transcript-"
        "specific HGVS strings, or (c) get a canonical genomic anchor before "
        "cross-database lookup. v1.0.0 accepts HGVS-only input; for rsid lookup, "
        "use `lookup_dbsnp` first and feed its primary HGVS form back in."
    ),
    input_model=NormalizeHgvsInput,
    output_model=NormalizeHgvsOutput,
    version="1.0.0",
    citations=[
        "Yates AD et al. (2020) Ensembl 2020. Nucleic Acids Res 48:D682-D688 (Ensembl REST API)",
        "den Dunnen JT et al. (2016) HGVS Recommendations for the Description of Sequence Variants: 2016 Update. Hum Mutat 37:564-569 (HGVS specification)",
    ],
    cost_hint="moderate",
    destructive=False,
    tags=["variants", "annotation", "hgvs", "normalize"],
)
async def normalize_hgvs(inp: NormalizeHgvsInput) -> NormalizeHgvsOutput:
    payload = await _fetch_variant_recoder(inp.hgvs, inp.species)
    alleles = _map_response(payload, inp.max_transcript_forms)

    caveats = list(_BASE_CAVEATS)
    if not alleles:
        caveats.append(
            f"Ensembl variant_recoder returned no allele records for {inp.hgvs!r}. "
            "This usually means the HGVS string parses but doesn't resolve to a "
            "known variant on the current Ensembl release."
        )
    for a in alleles:
        if a.total_hgvsc_count > len(a.hgvsc) or a.total_hgvsp_count > len(a.hgvsp):
            caveats.append(
                f"Allele {a.allele!r}: trimmed transcript forms to {inp.max_transcript_forms} "
                f"(of {a.total_hgvsc_count} hgvsc, {a.total_hgvsp_count} hgvsp). Raise "
                "`max_transcript_forms` to see more."
            )
            break  # one caveat is enough; don't repeat per-allele

    return NormalizeHgvsOutput(query=inp.hgvs, alleles=alleles, caveats=caveats)
