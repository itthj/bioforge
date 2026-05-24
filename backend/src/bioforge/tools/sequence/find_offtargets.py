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

_DNA_CHARS = set("ACGTNacgtn")


class FindOfftargetsInput(ToolInput):
    guide: str = Field(
        ...,
        min_length=15,
        max_length=25,
        description=(
            "Guide RNA protospacer sequence (DNA bases). Typically 20 nt for SpCas9. "
            "PAM is NOT included."
        ),
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
    alignment_length: int
    query_coverage_percent: float = Field(
        description="Percent of the guide that participated in the alignment."
    )
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
    blast_request_id: str = Field(
        description="NCBI RID from the underlying BLAST search — reproducibility handle."
    )
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
    mismatch_count: int, query_coverage_percent: float
) -> tuple[Literal["high", "medium", "low"], str]:
    """Simple risk classification. Real-world Cas9 off-target prediction is more
    nuanced — this matches common CRISPR-screen filtering heuristics."""
    if query_coverage_percent < 80:
        return (
            "low",
            f"Only {query_coverage_percent:.0f}% of the guide aligned — likely a "
            "partial match, not a complete off-target.",
        )
    if mismatch_count <= 1:
        return (
            "high",
            f"{mismatch_count} mismatch(es) across full guide. Cas9 tolerates up to "
            "~2 mismatches, especially in the 5' distal region. Strong off-target risk.",
        )
    if mismatch_count == 2:
        return (
            "high",
            "2 mismatches across full guide. Cas9 can still cleave depending on "
            "position — treat as high risk until seed-region location is confirmed.",
        )
    if mismatch_count == 3:
        return (
            "medium",
            "3 mismatches — cleavage possible but reduced; site-by-site validation "
            "recommended for therapeutic applications.",
        )
    return (
        "low",
        f"{mismatch_count} mismatches — unlikely to be cleaved by SpCas9 in most "
        "contexts.",
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
        raise ToolError(
            f"Underlying BLAST call failed: {type(e).__name__}: {e}"
        ) from e

    blast_dict = blast_result.model_dump()
    guide_len = len(inp.guide)
    all_hits = blast_dict.get("hits", [])
    request_id = blast_dict.get("request_id", "")

    candidates: list[OfftargetHit] = []
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

        risk, reason = _classify_risk(mismatches, coverage)
        candidates.append(
            OfftargetHit(
                accession=h.get("accession", ""),
                organism=h.get("organism"),
                subject_definition=h.get("definition", ""),
                mismatch_count=mismatches,
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

    # Sort: high risk first, then by mismatches ascending within each tier.
    _risk_order = {"high": 0, "medium": 1, "low": 2}
    candidates.sort(key=lambda c: (_risk_order[c.risk_label], c.mismatch_count))
    candidates = candidates[: inp.max_hits]

    high = sum(1 for c in candidates if c.risk_label == "high")
    medium = sum(1 for c in candidates if c.risk_label == "medium")
    low = sum(1 for c in candidates if c.risk_label == "low")

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
        caveats=[
            "PAM at each off-target site is NOT verified. BLAST matches the guide "
            "sequence but does not confirm a downstream PAM exists in the genome. A "
            "match without a PAM cannot be cleaved by Cas9. Future enhancement: "
            "fetch flanking context via Entrez efetch to verify PAMs.",
            "Risk labels are simple mismatch-count thresholds. They do NOT weight "
            "seed-region (PAM-proximal) mismatches more heavily than distal ones, "
            "though the seed is more important for Cas9 binding.",
            "Bulges / insertions / deletions in the off-target alignment are NOT "
            "considered — only substitution mismatches.",
            "BLAST coverage is sensitive to the database choice. Searching 'nt' "
            "finds matches across organisms, which is appropriate for cross-species "
            "concerns but inflates the hit count for any well-conserved sequence.",
        ],
    )
