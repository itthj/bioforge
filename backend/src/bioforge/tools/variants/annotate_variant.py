"""Predict the molecular consequences of a variant via Ensembl VEP REST.

First non-trivial Phase 3 tool. Given an HGVS notation (e.g.
`ENST00000357654.9:c.181T>G` for the BRCA1 p.Cys61Gly missense), the tool
calls Ensembl's public `/vep/{species}/hgvs/` endpoint and converts the
response into typed `VariantConsequence` rows + `ColocatedVariant` summaries
(ClinVar, dbSNP, gnomAD) so the agent can reason over them.

Why HGVS as the input:
  - HGVS is the canonical nomenclature in variant interpretation, and Ensembl
    accepts it directly (no need to translate to VCF-style first).
  - parse_vcf emits VCF-style positions; the agent can convert by composing
    `format_hgvs` (future slice) before calling this. We keep them decoupled.

What this tool surfaces:
  - One `VariantConsequence` per overlapping transcript: gene/biotype/impact,
    SO consequence terms, derived protein change ("C61G"), SIFT/PolyPhen
    scores when available.
  - `colocated_variants` — Ensembl returns ClinVar (`var_synonyms.ClinVar`),
    dbSNP (`id` like `rs28897672`), and gnomAD frequencies in the same
    response. This gives us ClinVar lookup essentially for free.
  - `canonical_consequence` — best single row to show in a quick summary
    (canonical transcript if marked, else first protein-coding hit).
  - Mandatory `caveats` — VEP predictions are computational, SIFT/PolyPhen
    are sequence-based and not the final word on pathogenicity, etc.

What this tool does NOT do:
  - Submit raw VCF lines (Ensembl has `/vep/{species}/region/` for that —
    separate slice if needed).
  - Predict structural-variant consequences.
  - Provide clinical interpretation. The output carries ClinVar
    cross-references; the agent's responder cites them, never paraphrases.

Network cost: one HTTPS call to rest.ensembl.org, typically 100ms-3s and
1-50 KB depending on how many transcripts overlap. cost_hint=moderate.
"""

from __future__ import annotations

import re
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, field_validator

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

ENSEMBL_REST_BASE = "https://rest.ensembl.org"

# Sequence Ontology consequence terms ranked HIGH > MODERATE > LOW > MODIFIER follow
# Ensembl's published impact scale — used only as a tie-breaker when picking the
# canonical row. The actual impact label comes from the API.
ImpactLevel = Literal["HIGH", "MODERATE", "LOW", "MODIFIER"]
_IMPACT_RANK: dict[str, int] = {"HIGH": 0, "MODERATE": 1, "LOW": 2, "MODIFIER": 3}

# HGVS accepts a lot of forms; we keep the validator permissive but reject obvious
# garbage. The real validation is whether Ensembl can resolve it — surfaced via the
# 400 error path.
_HGVS_RE = re.compile(r"^[A-Za-z0-9_.:>=+\-\[\]/]+$")


class AnnotateVariantInput(ToolInput):
    hgvs: str = Field(
        ...,
        min_length=4,
        max_length=200,
        description=(
            "HGVS notation for the variant. Accepts transcript-level (e.g. "
            "'ENST00000357654.9:c.181T>G'), RefSeq (e.g. 'NM_007294.4:c.181T>G'), "
            "or genomic (e.g. '17:g.43106487T>G'). The exact form must follow "
            "the HGVS spec; Ensembl returns 400 if it can't parse it and the "
            "tool surfaces that error verbatim."
        ),
    )
    species: str = Field(
        default="human",
        pattern=r"^[a-z_]+$",
        description=(
            "Ensembl species slug (lowercase, underscored). Defaults to 'human'. "
            "Examples: 'mus_musculus', 'rattus_norvegicus', 'danio_rerio'. "
            "Wrong slugs surface as HTTP 400."
        ),
    )
    include_regulatory: bool = Field(
        default=True,
        description=(
            "Include regulatory and motif feature consequences in the response. "
            "These appear when the variant falls inside an Ensembl Regulatory "
            "Build region (promoter, enhancer, TFBS). Disable if you only want "
            "the protein-level consequences."
        ),
    )

    @field_validator("hgvs")
    @classmethod
    def _validate_hgvs_shape(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("hgvs is empty")
        if not _HGVS_RE.match(stripped):
            raise ValueError(
                f"hgvs contains unexpected characters: {stripped!r}. Expected HGVS "
                "notation like 'ENST00000357654.9:c.181T>G' or '17:g.43106487T>G'."
            )
        return stripped


# --- Output schema ------------------------------------------------------------------


class VariantConsequence(BaseModel):
    """One transcript-level consequence prediction."""

    transcript_id: str = Field(description="Ensembl transcript ID (e.g. ENST00000357654).")
    gene_symbol: str | None = Field(default=None, description="HGNC gene symbol when known.")
    gene_id: str | None = Field(default=None, description="Ensembl gene ID (ENSG...).")
    biotype: str | None = Field(
        default=None,
        description=(
            "Transcript biotype — 'protein_coding', 'nonsense_mediated_decay', "
            "'retained_intron', 'lncRNA', etc. Drives how confident the protein "
            "change is — NMD transcripts won't actually produce protein."
        ),
    )
    impact: ImpactLevel | str | None = Field(
        default=None,
        description="VEP-assigned impact tier: HIGH / MODERATE / LOW / MODIFIER.",
    )
    consequence_terms: list[str] = Field(
        default_factory=list,
        description=(
            "Sequence Ontology terms describing the molecular consequence — "
            "e.g. 'missense_variant', 'stop_gained', 'splice_donor_variant'. "
            "Multiple can apply (a missense inside an NMD transcript yields "
            "['missense_variant', 'NMD_transcript_variant'])."
        ),
    )
    canonical: bool = Field(
        default=False,
        description="True if Ensembl marks this transcript as canonical for the gene.",
    )
    protein_change: str | None = Field(
        default=None,
        description=(
            "One-letter HGVS-style protein change derived from amino_acids + "
            "protein_start (e.g. 'C61G'). None when the variant isn't coding."
        ),
    )
    cdna_change: str | None = Field(
        default=None,
        description="Coding-DNA change derived from CDS positions (e.g. 'c.181T>G'). None when not coding.",
    )
    sift_score: float | None = Field(default=None, description="SIFT score (0=damaging, 1=tolerated).")
    sift_prediction: str | None = Field(default=None, description="SIFT label: 'deleterious', 'tolerated', etc.")
    polyphen_score: float | None = Field(default=None, description="PolyPhen-2 score (0=benign, 1=damaging).")
    polyphen_prediction: str | None = Field(
        default=None,
        description="PolyPhen-2 label: 'benign', 'possibly_damaging', 'probably_damaging'.",
    )
    distance: int | None = Field(
        default=None,
        description="For upstream/downstream variants: distance in bp from the transcript boundary.",
    )


class ColocatedVariant(BaseModel):
    """A previously-known variant overlapping the same locus.

    Ensembl populates this from dbSNP (rsIDs), ClinVar (RCV/VCV IDs in
    `var_synonyms.ClinVar`), HGMD, COSMIC, and the gnomAD frequency dump.
    The first rsID-style entry is typically the canonical dbSNP record;
    subsequent entries are clinical-significance records (HGMD/ClinVar).
    """

    id: str = Field(description="dbSNP rsID, ClinVar/HGMD/COSMIC accession, or other source ID.")
    clin_sig: list[str] = Field(
        default_factory=list,
        description=(
            "ClinVar significance terms aggregated by Ensembl — e.g. "
            "['pathogenic', 'likely_pathogenic']. Empty when no ClinVar record overlaps."
        ),
    )
    clinvar_accessions: list[str] = Field(
        default_factory=list,
        description="ClinVar RCV/VCV accessions from var_synonyms.ClinVar.",
    )
    gnomad_af: float | None = Field(
        default=None,
        description=(
            "Overall gnomAD exome allele frequency for the variant allele, when present. "
            "Sub-population breakdowns (gnomade_nfe, gnomadg_afr, etc.) are not surfaced "
            "individually — use the structured `frequencies_raw` field for those."
        ),
    )
    frequencies_raw: dict[str, dict[str, float]] = Field(
        default_factory=dict,
        description="Raw frequencies dict from Ensembl, keyed by allele then by sub-population.",
    )
    phenotype_or_disease: bool = Field(
        default=False,
        description="True if Ensembl flags this colocated variant as associated with a phenotype or disease.",
    )
    somatic: bool = Field(default=False, description="True for somatic variants (COSMIC entries).")
    pubmed_count: int = Field(
        default=0,
        description="How many PubMed citations Ensembl associates with this colocated variant. The full list is omitted to keep agent context lean.",
    )


class AnnotateVariantOutput(ToolOutput):
    input_hgvs: str = Field(description="Original HGVS string as submitted (post-validation).")
    assembly_name: str | None = Field(default=None, description="Reference genome assembly (e.g. 'GRCh38').")
    seq_region_name: str | None = Field(default=None, description="Chromosome / contig name.")
    start: int | None = Field(default=None, description="1-based genomic start.")
    end: int | None = Field(default=None, description="1-based genomic end (inclusive).")
    strand: int | None = Field(default=None, description="+1 or -1.")
    allele_string: str | None = Field(
        default=None,
        description="REF/ALT (or REF/ALT1/ALT2 for multi-allelic). E.g. 'T/G'.",
    )
    most_severe_consequence: str | None = Field(
        default=None,
        description="Ensembl-derived worst-impact consequence term across all overlapping transcripts.",
    )
    transcript_consequences: list[VariantConsequence] = Field(
        default_factory=list,
        description="One row per overlapping transcript. Sorted: canonical first, then by impact rank.",
    )
    canonical_consequence: VariantConsequence | None = Field(
        default=None,
        description=(
            "Convenience pointer to the row the agent should cite when summarizing: "
            "the canonical-transcript row when one is marked, otherwise the highest-"
            "impact protein-coding hit. None when no transcript consequences exist."
        ),
    )
    colocated_variants: list[ColocatedVariant] = Field(
        default_factory=list,
        description="Previously-known variants at the same locus — dbSNP, ClinVar, HGMD, COSMIC, gnomAD.",
    )
    clinvar_significance: list[str] = Field(
        default_factory=list,
        description=(
            "Deduplicated aggregate of clin_sig values across all colocated variants — "
            "the at-a-glance ClinVar verdict. Empty when no ClinVar record overlaps."
        ),
    )
    caveats: list[str] = Field(default_factory=list)


# --- HTTP, factored for test patching -----------------------------------------------


async def _fetch_vep(hgvs: str, species: str) -> list[dict[str, Any]]:
    """Call Ensembl REST `/vep/{species}/hgvs/{hgvs}` and return the JSON array.

    Ensembl returns a JSON array — one entry per submitted variant. We submit
    a single HGVS so we always expect a one-element list. Empty arrays come
    back when the HGVS resolved but no consequence overlaps (rare).

    Raises ToolError on any non-200 / network failure, with the body excerpt
    so the agent can react.
    """
    # Ensembl's URL parser is finicky about literal '>' — encode it so the
    # path stays unambiguous through proxies. httpx does this for us when
    # we pass via `params`, but the VEP endpoint embeds HGVS in the path,
    # so we manually quote.
    from urllib.parse import quote

    url = f"{ENSEMBL_REST_BASE}/vep/{species}/hgvs/{quote(hgvs, safe='')}"
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
        body = resp.text[:400]
        raise ToolError(
            f"Ensembl rejected the HGVS notation {hgvs!r} (HTTP 400). "
            f"Server message: {body!r}. Verify the syntax — transcript IDs must "
            "include the version suffix (e.g. 'ENST00000357654.9'), and 'c.' / "
            "'g.' prefixes must match the reference (CDS vs genomic)."
        )
    if resp.status_code == 404:
        raise ToolError(
            f"Ensembl could not resolve the variant {hgvs!r} for species {species!r} (HTTP 404). "
            "Possible reasons: the transcript ID doesn't exist, the position is outside the "
            "transcript bounds, or the species slug is wrong."
        )
    if resp.status_code == 429:
        raise ToolError(
            "Ensembl REST rate-limited the request (HTTP 429). Wait a few seconds and retry; "
            "consider switching to a self-hosted VEP instance for high-volume annotation."
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


# --- Mapping --------------------------------------------------------------------------


def _derive_protein_change(amino_acids: str | None, protein_start: int | None) -> str | None:
    """Convert Ensembl's `amino_acids='C/G'` + `protein_start=61` into 'C61G'.

    Returns None if either input is missing or the amino_acids field doesn't have a '/'
    (insertions, deletions, and synonymous changes use different layouts that we
    don't synthesize at the protein level — they're already in consequence_terms).
    """
    if not amino_acids or protein_start is None or "/" not in amino_acids:
        return None
    ref, _, alt = amino_acids.partition("/")
    if not ref or not alt or len(ref) != 1 or len(alt) != 1:
        # Multi-residue substitutions / frameshifts go via consequence_terms.
        return None
    return f"{ref}{protein_start}{alt}"


def _derive_cdna_change(
    cds_start: int | None,
    cds_end: int | None,
    allele_string: str | None,
) -> str | None:
    """Construct 'c.{pos}{ref}>{alt}' for simple substitutions.

    Returns None for indels / multi-allelic / missing-position cases — those have
    HGVS forms (c.123_124delAT) we don't generate by hand to avoid being wrong.
    """
    if cds_start is None or allele_string is None or "/" not in allele_string:
        return None
    if cds_end is not None and cds_start != cds_end:
        return None
    ref, _, alt = allele_string.partition("/")
    if "/" in alt:  # multi-allelic
        return None
    if not ref or not alt or len(ref) != 1 or len(alt) != 1:
        return None
    return f"c.{cds_start}{ref}>{alt}"


def _map_transcript_consequence(raw: dict[str, Any], allele_string: str | None) -> VariantConsequence:
    return VariantConsequence(
        transcript_id=raw.get("transcript_id", "<unknown>"),
        gene_symbol=raw.get("gene_symbol"),
        gene_id=raw.get("gene_id"),
        biotype=raw.get("biotype"),
        impact=raw.get("impact"),
        consequence_terms=list(raw.get("consequence_terms", [])),
        canonical=bool(raw.get("canonical", False)),
        protein_change=_derive_protein_change(raw.get("amino_acids"), raw.get("protein_start")),
        cdna_change=_derive_cdna_change(raw.get("cds_start"), raw.get("cds_end"), allele_string),
        sift_score=raw.get("sift_score"),
        sift_prediction=raw.get("sift_prediction"),
        polyphen_score=raw.get("polyphen_score"),
        polyphen_prediction=raw.get("polyphen_prediction"),
        distance=raw.get("distance"),
    )


def _map_colocated_variant(raw: dict[str, Any]) -> ColocatedVariant:
    frequencies = raw.get("frequencies", {}) or {}
    # Pull the alt-allele gnomAD freq if present. Ensembl puts it under the allele letter.
    gnomad_af: float | None = None
    for allele_freqs in frequencies.values():
        if isinstance(allele_freqs, dict) and "gnomade" in allele_freqs:
            gnomad_af = float(allele_freqs["gnomade"])
            break
    var_syn = raw.get("var_synonyms", {}) or {}
    clinvar_acc = list(var_syn.get("ClinVar", [])) if isinstance(var_syn.get("ClinVar"), list) else []
    return ColocatedVariant(
        id=raw.get("id", "<unknown>"),
        clin_sig=list(raw.get("clin_sig", [])),
        clinvar_accessions=clinvar_acc,
        gnomad_af=gnomad_af,
        frequencies_raw=frequencies,
        phenotype_or_disease=bool(raw.get("phenotype_or_disease", False)),
        somatic=bool(raw.get("somatic", False)),
        pubmed_count=len(raw.get("pubmed", [])),
    )


def _pick_canonical_consequence(rows: list[VariantConsequence]) -> VariantConsequence | None:
    """Choose the best single row for at-a-glance display.

    Preference order:
      1. Canonical transcript (Ensembl-marked).
      2. Protein-coding biotype with highest impact.
      3. First row by impact rank (fallback).
    """
    if not rows:
        return None
    canonicals = [r for r in rows if r.canonical]
    if canonicals:
        # Multiple canonicals shouldn't happen but rank just in case.
        return min(canonicals, key=lambda r: _IMPACT_RANK.get(r.impact or "MODIFIER", 99))
    coding = [r for r in rows if r.biotype == "protein_coding"]
    if coding:
        return min(coding, key=lambda r: _IMPACT_RANK.get(r.impact or "MODIFIER", 99))
    return min(rows, key=lambda r: _IMPACT_RANK.get(r.impact or "MODIFIER", 99))


def _sorted_consequences(rows: list[VariantConsequence]) -> list[VariantConsequence]:
    """Canonical first, then by impact rank (HIGH → MODIFIER), then by gene+transcript ID."""
    return sorted(
        rows,
        key=lambda r: (
            0 if r.canonical else 1,
            _IMPACT_RANK.get(r.impact or "MODIFIER", 99),
            r.gene_symbol or "",
            r.transcript_id,
        ),
    )


_BASE_CAVEATS = [
    "VEP consequences are computational predictions based on transcript structure. They are NOT clinical assertions of pathogenicity — use ClinVar / variant-specific literature for that.",
    "SIFT and PolyPhen-2 are sequence-based predictors with documented false-positive and false-negative rates. Treat their labels as one input to interpretation, not as ground truth.",
    "Consequences in NMD-targeted transcripts (biotype='nonsense_mediated_decay') reflect the predicted RNA — the protein change is usually never expressed because NMD degrades the mRNA.",
    "Colocated variants come from Ensembl's join with dbSNP, ClinVar, HGMD, COSMIC, and gnomAD at indexing time. Records added since the last Ensembl release won't appear; verify clinical-significance claims at https://www.ncbi.nlm.nih.gov/clinvar/ directly when stakes are high.",
]


# --- Tool ---------------------------------------------------------------------------


@register_tool(
    name="annotate_variant",
    description=(
        "Predict the molecular consequences of a variant via Ensembl VEP. "
        "Input: an HGVS notation (transcript-, RefSeq-, or genomic-level). "
        "Returns: one VariantConsequence per overlapping transcript "
        "(gene, biotype, impact, SO terms, derived protein/cDNA changes, "
        "SIFT/PolyPhen scores when available), a `canonical_consequence` "
        "pointer for at-a-glance summary, and `colocated_variants` carrying "
        "dbSNP rsIDs, ClinVar accessions + significance, gnomAD allele "
        "frequencies, and HGMD/COSMIC cross-references that come back in the "
        "same Ensembl response. Use whenever the user asks 'what does this "
        "variant do?' / 'is this variant pathogenic?' / 'what's the gene "
        "context?'. Composes with parse_vcf: parse a VCF, then call this on "
        "each variant of interest."
    ),
    input_model=AnnotateVariantInput,
    output_model=AnnotateVariantOutput,
    version="1.0.0",
    citations=[
        "McLaren W et al. (2016) The Ensembl Variant Effect Predictor. Genome Biology 17:122 (VEP)",
        "Yates AD et al. (2020) Ensembl 2020. Nucleic Acids Res 48:D682-D688 (Ensembl release pipeline)",
        "Karczewski KJ et al. (2020) The mutational constraint spectrum quantified from variation in 141,456 humans. Nature 581:434-443 (gnomAD; colocated_variants.frequencies)",
        "Landrum MJ et al. (2018) ClinVar: improving access to variant interpretations and supporting evidence. Nucleic Acids Res 46:D1062-D1067 (var_synonyms.ClinVar)",
    ],
    cost_hint="moderate",
    destructive=False,
    tags=["variants", "annotation", "vep", "clinvar"],
)
async def annotate_variant(inp: AnnotateVariantInput) -> AnnotateVariantOutput:
    payload = await _fetch_vep(inp.hgvs, inp.species)

    if not payload:
        return AnnotateVariantOutput(
            input_hgvs=inp.hgvs,
            caveats=_BASE_CAVEATS + ["Ensembl returned an empty result for this HGVS — no overlapping consequence."],
        )

    first = payload[0]
    if not isinstance(first, dict):
        raise ToolError(f"Ensembl REST returned non-object payload element: {type(first).__name__}")

    allele_string = first.get("allele_string")
    raw_tx = first.get("transcript_consequences", []) or []
    raw_colocated = first.get("colocated_variants", []) or []

    consequences = [_map_transcript_consequence(r, allele_string) for r in raw_tx if isinstance(r, dict)]
    consequences = _sorted_consequences(consequences)
    canonical = _pick_canonical_consequence(consequences)

    colocated = [_map_colocated_variant(c) for c in raw_colocated if isinstance(c, dict)]

    # Aggregate ClinVar significance across all colocated variants — dedup preserve-order.
    seen: set[str] = set()
    clinvar_sig: list[str] = []
    for cv in colocated:
        for term in cv.clin_sig:
            if term not in seen:
                seen.add(term)
                clinvar_sig.append(term)

    caveats = list(_BASE_CAVEATS)
    if not consequences:
        caveats.append(
            "No transcript-level consequence rows were returned for this variant. The "
            "site may lie in an intergenic region or in a feature class VEP doesn't model."
        )
    if any(r.biotype == "nonsense_mediated_decay" for r in consequences):
        caveats.append(
            "Some consequences are predicted in NMD transcripts — see biotype "
            "field on each row; NMD-targeted mRNAs typically don't produce protein."
        )

    return AnnotateVariantOutput(
        input_hgvs=inp.hgvs,
        assembly_name=first.get("assembly_name"),
        seq_region_name=first.get("seq_region_name"),
        start=first.get("start"),
        end=first.get("end"),
        strand=first.get("strand"),
        allele_string=allele_string,
        most_severe_consequence=first.get("most_severe_consequence"),
        transcript_consequences=consequences,
        canonical_consequence=canonical,
        colocated_variants=colocated,
        clinvar_significance=clinvar_sig,
        caveats=caveats,
    )
