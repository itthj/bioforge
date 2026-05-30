"""CRISPR off-target site search — composite tool that calls `blast` internally.

This is the first tool in BioForge that composes other tools: it accepts a guide RNA,
runs `blast` against a chosen database via `execute_tool`, then parses + ranks the hits
as off-target candidates.

# Composition pattern

Calling another tool through `execute_tool` (rather than importing the handler directly)
gives us:
  - The inner tool's input validation (BlastInput's alphabet check, length bounds, etc.)
  - The inner tool's provenance stamping (tool_name="blast", version, citations) which
    we surface in our own output as `blast_request_id` and inherited citations
  - A single audited entry point — when we add OTel tracing, the inner blast call shows
    up as a child span automatically

# Honesty: what this tool does NOT do

  - **PAM verification at off-target sites.** A BLAST hit means "the guide sequence
    matches here with N mismatches" — it does NOT confirm the genome has a valid PAM
    downstream of the match. Cas9 won't cut without a PAM. Full PAM verification
    requires fetching each hit's flanking sequence via Entrez efetch — that's a future
    slice. The current tool labels this clearly in `caveats`.
  - **Seed-region weighting.** Mismatches in the PAM-proximal seed region (positions
    1-12 from the 3' end of the protospacer) are MORE disruptive to Cas9 binding than
    mismatches in the 5' distal region. We report mismatch_count as a single number;
    seed-vs-distal weighting is future work.
  - **Bulges / indels.** This tool only considers substitution mismatches in the BLAST
    alignment, not insertions/deletions in the off-target.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from bioforge.config import settings
from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import execute_tool, register_tool
from bioforge.tools.sequence.offtarget_pam import (
    FLANK_MARGIN,
    OfftargetPamError,
    efetch_flank,
    extract_pam,
)
from bioforge.tools.sequence.offtarget_scoring import (
    cfd_mismatch_component,
    cfd_score,
    score_offtarget,
)

_DNA_CHARS = set("ACGTNacgtn")
_ACGT = set("ACGT")


class FindOfftargetsInput(ToolInput):
    guide: str = Field(
        ...,
        min_length=15,
        max_length=25,
        description=("Guide RNA protospacer sequence (DNA bases). Typically 20 nt for SpCas9. PAM is NOT included."),
    )
    database: str = Field(
        default="nt",
        max_length=64,
        description=(
            "NCBI database to search. 'nt' for cross-organism nucleotide search; "
            "'refseq_genomic' for curated reference genomes only; an organism-specific "
            "database for a focused search."
        ),
    )
    max_mismatches: int = Field(
        default=4,
        ge=0,
        le=10,
        description=(
            "Maximum mismatches in the alignment for a hit to be reported as an off-"
            "target candidate. Default 4 follows common CRISPR design practice — sites "
            "with more mismatches are unlikely to be cleaved."
        ),
    )
    max_hits: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Cap on returned hits, after filtering by max_mismatches.",
    )
    blast_max_hits: int = Field(
        default=50,
        ge=10,
        le=200,
        description=(
            "How many raw BLAST hits to retrieve before mismatch-filtering. Larger "
            "values catch more potential off-targets at the cost of latency."
        ),
    )
    expect_threshold: float = Field(
        default=1000.0,
        gt=0,
        le=10_000,
        description=(
            "BLAST E-value threshold. Short queries (20 nt) need a high E to surface "
            "weak hits — these are the high-mismatch candidates that matter for off-"
            "target analysis."
        ),
    )
    verify_pam: bool = Field(
        default=False,
        description=(
            "When true, fetch each clean off-target's flanking genome (NCBI Entrez efetch) to read "
            "and verify its PAM, then report the FULL CFD score (mismatch × PAM-activity) in "
            "`cfd_full_score`. Adds one network fetch per clean hit (slower). Off by default — "
            "without it only the PAM-free CFD mismatch component is reported. A PAM that cannot be "
            "soundly reconstructed from the genome is left unverified, never guessed."
        ),
    )

    @field_validator("guide")
    @classmethod
    def _validate_dna(cls, v: str) -> str:
        cleaned = "".join(v.split()).upper()
        if not cleaned:
            raise ValueError("guide is empty after stripping whitespace")
        bad = set(cleaned) - {c.upper() for c in _DNA_CHARS}
        if bad:
            raise ValueError(f"guide contains non-DNA characters: {sorted(bad)!r}")
        return cleaned


class OfftargetHit(BaseModel):
    accession: str
    organism: str | None
    subject_definition: str = Field(description="The hit's FASTA defline.")
    mismatch_count: int
    mismatch_positions: list[int] = Field(
        default_factory=list,
        description=(
            "1-based positions along the guide (1=5' end, 20=PAM-adjacent for a "
            "20-nt guide) where the off-target differs. Empty if the alignment "
            "strings were unavailable and we had to fall back to a count-only "
            "approximation — see `used_full_alignment`."
        ),
    )
    mit_score: float = Field(
        description=(
            "Hsu 2013 MIT off-target score in [0, 1]. Higher = greater cleavage "
            "likelihood. >0.2 typically signals real off-target risk; >0.5 is "
            "high concern. 1.0 = perfect match."
        ),
    )
    used_full_alignment: bool = Field(
        description=(
            "True if mit_score was computed from per-position mismatch info. "
            "False = optimistic fallback (mismatches assumed to be at the "
            "PAM-distal end, which under-estimates risk)."
        ),
    )
    cfd_mismatch_score: float | None = Field(
        default=None,
        description=(
            "Doench-2016 CFD mismatch-tolerance component in [0,1] (base-identity-weighted; the "
            "field-standard off-target metric, slightly outperforms MIT). Higher = more likely "
            "cleaved. NOTE: this is the MISMATCH component only -- the off-target PAM is not "
            "verified, so CFD's PAM-activity factor is not applied (treat as an upper bound). "
            "null for gapped / partial / non-ACGT alignments. See cfd_full_score when verify_pam=true."
        ),
    )
    pam: str | None = Field(
        default=None,
        description=(
            "The off-target's 3-nt PAM (NGG) read from the genome 3' of the protospacer on the "
            "matching strand and verified by reconstruction. Populated only when verify_pam=true and "
            "the locus reconstructed soundly; null otherwise (never a guessed PAM)."
        ),
    )
    cfd_full_score: float | None = Field(
        default=None,
        description=(
            "FULL Doench-2016 CFD score in [0,1] = the mismatch component × the PAM-activity weight "
            "for the VERIFIED off-target PAM. Populated only when verify_pam=true and the PAM was "
            "verified; null otherwise (fall back to cfd_mismatch_score, the upper bound). A low value "
            "on a strong-mismatch hit often means a weak/non-canonical PAM → unlikely to be cleaved."
        ),
    )
    alignment_length: int
    query_coverage_percent: float = Field(description="Percent of the guide that participated in the alignment.")
    identity_percent: float
    e_value: float
    bit_score: float
    subject_start: int
    subject_end: int
    risk_label: Literal["high", "medium", "low"]
    risk_reason: str


class FindOfftargetsOutput(ToolOutput):
    guide: str
    guide_length: int
    database: str
    blast_request_id: str = Field(description="NCBI RID from the underlying BLAST search — reproducibility handle.")
    num_blast_hits_total: int
    num_offtargets_returned: int
    high_risk_count: int
    medium_risk_count: int
    low_risk_count: int
    hits: list[OfftargetHit]
    caveats: list[str] = Field(
        default_factory=list,
        description="Caveats the responder is required to surface to the user.",
    )


def _classify_risk(
    *,
    mismatch_count: int,
    mit_score: float,
    mismatch_positions: list[int],
    guide_length: int,
    query_coverage_percent: float,
    used_full_alignment: bool,
) -> tuple[Literal["high", "medium", "low"], str]:
    """Risk classification using both MIT score and qualitative reasoning.

    Thresholds:
      - high: mit_score >= 0.5
      - medium: 0.1 <= mit_score < 0.5
      - low: mit_score < 0.1 OR query_coverage_percent < 80

    These thresholds are widely used in published CRISPR design tools (CHOPCHOP,
    CRISPOR) and balance sensitivity / specificity for cell-culture screens.
    Therapeutic applications typically use stricter thresholds (high if
    mit_score >= 0.05).
    """
    if query_coverage_percent < 80:
        return (
            "low",
            f"Only {query_coverage_percent:.0f}% of the guide aligned — likely a "
            "partial match, not a complete off-target.",
        )

    # Identify whether mismatches sit in the seed region (PAM-proximal half).
    # For a 20-nt guide, seed = positions 11-20.
    seed_cutoff = max(1, guide_length // 2 + 1)
    seed_mismatches = sum(1 for p in mismatch_positions if p >= seed_cutoff)
    distal_mismatches = len(mismatch_positions) - seed_mismatches

    seed_note = ""
    if mismatch_positions:
        seed_note = (
            f" {seed_mismatches} of {len(mismatch_positions)} mismatch(es) fall in "
            f"the seed region (positions {seed_cutoff}-{guide_length})."
        )

    fallback_note = ""
    if not used_full_alignment:
        fallback_note = (
            " (MIT score computed from mismatch count only — actual positions "
            "unavailable; score may UNDER-estimate risk.)"
        )

    if mit_score >= 0.5:
        return (
            "high",
            f"MIT score {mit_score:.3f} with {mismatch_count} mismatch(es)."
            f"{seed_note} Strong off-target risk.{fallback_note}",
        )
    if mit_score >= 0.1:
        # Two mismatches placed at PAM-distal end produce a high MIT score
        # (~0.97) — still high risk. So this bucket really means
        # "many mismatches, mostly seed, score still non-trivial".
        if seed_mismatches >= 2:
            return (
                "medium",
                f"MIT score {mit_score:.3f}, {seed_mismatches} seed mismatch(es)."
                " Cleavage possible but reduced — site-by-site validation"
                f" recommended for therapeutic applications.{seed_note}{fallback_note}",
            )
        return (
            "high",
            f"MIT score {mit_score:.3f} — mismatches concentrated outside the seed."
            f" Still expected to cleave.{seed_note}{fallback_note}",
        )
    if distal_mismatches > 0 and seed_mismatches == 0:
        return (
            "low",
            f"MIT score {mit_score:.3f}: all {distal_mismatches} mismatch(es) in "
            "the PAM-distal region — typically tolerated by Cas9, but seed-only "
            f"interpretation means real activity is uncertain.{fallback_note}",
        )
    return (
        "low",
        f"MIT score {mit_score:.3f} with {mismatch_count} mismatch(es)."
        f"{seed_note} Unlikely to be cleaved by SpCas9 in most contexts.{fallback_note}",
    )


async def _verify_pam_for_hit(
    *,
    accession: str,
    subject_start: int,
    subject_end: int,
    on_target: str,
    off_target: str,
    email: str,
) -> tuple[str | None, float | None]:
    """Fetch the off-target flank, verify its PAM, and compute the FULL CFD score.

    Returns `(pam3, cfd_full_score)`, or `(None, None)` on a missing accession, a fetch failure, an
    unsound reconstruction (the §0 soundness gate in `extract_pam`), or an unscorable input — in
    every such case the caller keeps the mismatch-only component rather than emit a guessed PAM.
    The efetch runs in a worker thread so the event loop stays free (mirrors the blast call).
    """
    if not accession:
        return None, None
    lo = min(subject_start, subject_end)
    hi = max(subject_start, subject_end)
    win_lo = max(1, lo - FLANK_MARGIN)
    win_hi = hi + FLANK_MARGIN
    try:
        window = await asyncio.to_thread(
            efetch_flank, accession=accession, seq_start=win_lo, seq_stop=win_hi, email=email
        )
    except OfftargetPamError:
        return None, None
    ext = extract_pam(
        window_plus=window,
        window_start=win_lo,
        subject_start=subject_start,
        subject_end=subject_end,
        guide_len=len(off_target),
        expected_protospacer=off_target,
    )
    if ext is None:
        return None, None
    try:
        full = cfd_score(on_target, off_target, ext.pam2)
    except ValueError:
        return None, None
    return ext.pam3, round(full, 4)


@register_tool(
    name="find_offtargets",
    description=(
        "Find potential off-target sites for a CRISPR guide RNA by BLAST-searching the "
        "guide against a reference database. Returns ranked hits annotated with mismatch "
        "count, query coverage, and a simple risk label (high/medium/low). Use after "
        "selecting a guide with `design_guides` to assess specificity, or whenever the "
        "user asks 'what else does this guide cut?' / 'where else does this sequence "
        "match?'. Set verify_pam=true to additionally fetch each clean off-target's genomic flank, "
        "verify its PAM, and report the FULL CFD score (mismatch × PAM) in cfd_full_score (one extra "
        "network fetch per clean hit). EXPENSIVE: this tool internally calls `blast` against NCBI's "
        "public service, which takes 30 seconds to several minutes. The user will be prompted "
        "for approval before the search runs."
    ),
    input_model=FindOfftargetsInput,
    output_model=FindOfftargetsOutput,
    version="1.0.0",
    citations=[
        "Hsu PD et al. (2013) DNA targeting specificity of RNA-guided Cas9 nucleases. Nat Biotechnol 31:827-832 (mismatch tolerance)",
        "Doench JG et al. (2016) Optimized sgRNA design to maximize activity and minimize off-target effects of CRISPR-Cas9. Nat Biotechnol 34:184-191 (CFD)",
        "CFD scoring weights: Doench 2016 values via maximilianh/crisporWebsite CFD_Scoring, committed + checksummed at data/cfd_doench2016.json",
        "Inherits from the `blast` tool: NCBI BLAST (Altschul 1990); Biopython qblast",
    ],
    cost_hint="expensive",
    destructive=False,
    tags=["sequence", "crispr", "search", "offtarget"],
    model_versions={"mit_offtarget": "hsu-2013-mit-score", "cfd_offtarget": "cfd-doench-2016"},
    emits_instance_uncertainty={"mit_offtarget": False, "cfd_offtarget": False},
    published_accuracy={
        "mit_offtarget": (
            "Hsu 2013 per-position MIT specificity score: a published deterministic weighting, "
            "not a trained predictor with a standalone held-out accuracy."
        ),
        "cfd_offtarget": (
            "CFD (Doench 2016) base-identity mismatch weighting, sourced verbatim from CRISPOR's "
            "CFD_Scoring. Deterministic published weights. Reported as the mismatch component by "
            "default; with verify_pam=true the off-target PAM is fetched + verified and the FULL CFD "
            "(mismatch × PAM-activity) is reported in cfd_full_score."
        ),
    },
    training_distribution={
        "nuclease": "SpCas9",
        "guide_length_nt": 20,
        "note": "deterministic published weights (Hsu 2013), not a trained model",
    },
    reference_data_keys=["ncbi_blast"],
)
async def find_offtargets(inp: FindOfftargetsInput) -> FindOfftargetsOutput:
    # Compose: call the blast tool through the registry. blastn-short is essential for
    # short queries — megablast (the NCBI default) misses most 20-nt hits.
    try:
        blast_result = await execute_tool(
            "blast",
            {
                "sequence": inp.guide,
                "program": "blastn",
                "task": "blastn-short",
                "database": inp.database,
                "expect_threshold": inp.expect_threshold,
                "max_hits": inp.blast_max_hits,
            },
        )
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(f"Underlying BLAST call failed: {type(e).__name__}: {e}") from e

    blast_dict = blast_result.model_dump()
    guide_len = len(inp.guide)
    all_hits = blast_dict.get("hits", [])
    request_id = blast_dict.get("request_id", "")

    candidates: list[OfftargetHit] = []
    any_fallback_used = False
    for h in all_hits:
        align_len = h.get("alignment_length", 0)
        if align_len == 0:
            continue
        identity_pct = h.get("identity_percent", 0.0)
        identities = round(align_len * identity_pct / 100.0)
        mismatches = align_len - identities
        coverage = round(100.0 * align_len / guide_len, 1)

        if mismatches > inp.max_mismatches:
            continue

        # Compute MIT score using the alignment strings BLAST exposes. If
        # they're missing (older fixtures), we fall back to a count-only
        # approximation and flag it on the hit.
        score = score_offtarget(
            guide_seq=inp.guide,
            query_aligned=h.get("query_aligned", "") or "",
            subject_aligned=h.get("subject_aligned", "") or "",
            mismatch_count_fallback=mismatches,
        )
        if not score.used_full_alignment:
            any_fallback_used = True

        # CFD mismatch component (Doench 2016) — only for a clean, full-length, gap-free, ACGT
        # alignment. The off-target PAM isn't fetched, so the PAM-activity factor is omitted.
        q_aln = (h.get("query_aligned", "") or "").upper()
        s_aln = (h.get("subject_aligned", "") or "").upper()
        cfd_mm: float | None = None
        if (
            score.used_full_alignment
            and len(q_aln) == len(s_aln) == guide_len
            and "-" not in q_aln
            and "-" not in s_aln
            and set(s_aln) <= _ACGT
            and set(q_aln) <= _ACGT
        ):
            try:
                cfd_mm = round(cfd_mismatch_component(q_aln, s_aln), 4)
            except ValueError:
                cfd_mm = None

        # Full CFD needs the off-target PAM, which BLAST doesn't return. When asked, fetch + verify
        # it from the genome for clean (CFD-scorable) hits; failures degrade to mismatch-only.
        pam_verified: str | None = None
        cfd_full: float | None = None
        if inp.verify_pam and cfd_mm is not None:
            pam_verified, cfd_full = await _verify_pam_for_hit(
                accession=h.get("accession", ""),
                subject_start=int(h.get("subject_start", 0) or 0),
                subject_end=int(h.get("subject_end", 0) or 0),
                on_target=q_aln,
                off_target=s_aln,
                email=settings.entrez_email,
            )

        risk, reason = _classify_risk(
            mismatch_count=mismatches,
            mit_score=score.score,
            mismatch_positions=score.mismatch_positions,
            guide_length=guide_len,
            query_coverage_percent=coverage,
            used_full_alignment=score.used_full_alignment,
        )
        candidates.append(
            OfftargetHit(
                accession=h.get("accession", ""),
                organism=h.get("organism"),
                subject_definition=h.get("definition", ""),
                mismatch_count=mismatches,
                mismatch_positions=score.mismatch_positions,
                mit_score=round(score.score, 4),
                cfd_mismatch_score=cfd_mm,
                pam=pam_verified,
                cfd_full_score=cfd_full,
                used_full_alignment=score.used_full_alignment,
                alignment_length=align_len,
                query_coverage_percent=coverage,
                identity_percent=identity_pct,
                e_value=h.get("e_value", 0.0),
                bit_score=h.get("bit_score", 0.0),
                subject_start=h.get("subject_start", 0),
                subject_end=h.get("subject_end", 0),
                risk_label=risk,
                risk_reason=reason,
            )
        )

    # Sort: by MIT score descending (highest cleavage risk first), then by
    # mismatch count ascending as a tiebreaker.
    candidates.sort(key=lambda c: (-c.mit_score, c.mismatch_count))
    candidates = candidates[: inp.max_hits]

    high = sum(1 for c in candidates if c.risk_label == "high")
    medium = sum(1 for c in candidates if c.risk_label == "medium")
    low = sum(1 for c in candidates if c.risk_label == "low")

    if inp.verify_pam:
        n_verified = sum(1 for c in candidates if c.pam is not None)
        pam_caveats = [
            f"PAM verification was ON: for {n_verified} of {len(candidates)} returned hit(s) the "
            "off-target PAM was fetched from the genome (Entrez efetch) and verified by reconstructing "
            "the protospacer at that locus, and cfd_full_score (mismatch × PAM-activity) is reported. "
            "Hits without a verified PAM (contig edge, fetch failure, or a reconstruction that did not "
            "match) keep only cfd_mismatch_score and a null pam — a PAM is never guessed. SpCas9 needs "
            "a canonical NGG PAM to cleave.",
            "Two off-target metrics: the Hsu-2013 MIT score (position-weighted) and the Doench-2016 CFD "
            "score. With a verified PAM, cfd_full_score is the FULL CFD; otherwise cfd_mismatch_score is "
            "the mismatch component only (an upper bound). Both are null for gapped/partial/non-ACGT alignments.",
        ]
    else:
        pam_caveats = [
            "PAM at each off-target site is NOT verified. BLAST matches the guide sequence but does not "
            "confirm a downstream NGG PAM exists in the genome; a match without a PAM cannot be cleaved "
            "by Cas9. Set verify_pam=true to fetch + verify each PAM and get the full CFD score.",
            "Two off-target metrics are reported: the Hsu-2013 MIT score (position-weighted) and the "
            "Doench-2016 CFD mismatch component (base-identity-weighted; field standard). The CFD value "
            "is the MISMATCH component only -- the off-target PAM is NOT verified, so CFD's PAM-activity "
            "factor is not applied; treat it as an upper bound. cfd_mismatch_score is null for "
            "gapped / partial / non-ACGT alignments.",
        ]
    caveats = [
        *pam_caveats,
        "Bulges / insertions / deletions in the off-target alignment are treated "
        "as position-aligned mismatches, NOT as separate indel events.",
        "BLAST coverage is sensitive to the database choice. Searching 'nt' "
        "finds matches across organisms, which is appropriate for cross-species "
        "concerns but inflates the hit count for any well-conserved sequence.",
    ]
    if any_fallback_used:
        caveats.append(
            "Some hits had no per-position alignment available (older BLAST "
            "format / fallback path). Their MIT score is an optimistic estimate "
            "that may UNDER-state real cleavage risk — flagged per-hit via "
            "`used_full_alignment=False`."
        )

    return FindOfftargetsOutput(
        guide=inp.guide,
        guide_length=guide_len,
        database=inp.database,
        blast_request_id=request_id,
        num_blast_hits_total=len(all_hits),
        num_offtargets_returned=len(candidates),
        high_risk_count=high,
        medium_risk_count=medium,
        low_risk_count=low,
        hits=candidates,
        caveats=caveats,
    )
