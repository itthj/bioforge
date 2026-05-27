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

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import execute_tool, register_tool
from bioforge.tools.sequence.offtarget_scoring import score_offtarget

_DNA_CHARS = set("ACGTNacgtn")


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


@register_tool(
    name="find_offtargets",
    description=(
        "Find potential off-target sites for a CRISPR guide RNA by BLAST-searching the "
        "guide against a reference database. Returns ranked hits annotated with mismatch "
        "count, query coverage, and a simple risk label (high/medium/low). Use after "
        "selecting a guide with `design_guides` to assess specificity, or whenever the "
        "user asks 'what else does this guide cut?' / 'where else does this sequence "
        "match?'. EXPENSIVE: this tool internally calls `blast` against NCBI's public "
        "service, which takes 30 seconds to several minutes. The user will be prompted "
        "for approval before the search runs."
    ),
    input_model=FindOfftargetsInput,
    output_model=FindOfftargetsOutput,
    version="1.0.0",
    citations=[
        "Hsu PD et al. (2013) DNA targeting specificity of RNA-guided Cas9 nucleases. Nat Biotechnol 31:827-832 (mismatch tolerance)",
        "Doench JG et al. (2016) Optimized sgRNA design to maximize activity and minimize off-target effects of CRISPR-Cas9. Nat Biotechnol 34:184-191",
        "Inherits from the `blast` tool: NCBI BLAST (Altschul 1990); Biopython qblast",
    ],
    cost_hint="expensive",
    destructive=False,
    tags=["sequence", "crispr", "search", "offtarget"],
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

    caveats = [
        "PAM at each off-target site is NOT verified. BLAST matches the guide "
        "sequence but does not confirm a downstream PAM exists in the genome. A "
        "match without a PAM cannot be cleaved by Cas9. Future enhancement: "
        "fetch flanking context via Entrez efetch to verify PAMs.",
        "MIT scores use Hsu 2013's per-position weights for SpCas9. They do NOT "
        "model the specific identity of mismatch base pairs (CFD scoring from "
        "Doench 2016 does — a future slice).",
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
