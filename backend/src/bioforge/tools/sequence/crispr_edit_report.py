"""End-to-end CRISPR edit report.

This is a composite workflow tool. It does not invent a new scoring model; it orchestrates
the lower-level audited tools that already exist:

  1. `design_guides` finds candidate guides and optionally computes on-target scores.
  2. `find_offtargets` can BLAST-search the top candidates for specificity risk.
  3. `edit_outcome` simulates deterministic NHEJ products for the top candidates.

The output is shaped for a biologist making an experiment decision: ranked candidate
guides, a recommendation label, a concise off-target summary, expected edit outcomes,
and caveats that the agent's responder must surface.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import execute_tool, register_tool

_DNA_CHARS = set("ACGTNacgtn")


class CrisprEditReportInput(ToolInput):
    target: str = Field(
        ...,
        min_length=30,
        description=(
            "Target DNA locus to edit. Include enough flanking sequence around the "
            "intended edit site for guide design and edit-outcome simulation."
        ),
    )
    pam: str = Field(
        default="NGG",
        min_length=2,
        max_length=10,
        description="PAM motif using IUPAC codes. Default NGG for SpCas9.",
    )
    max_guides: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Number of guide candidates to evaluate in the report.",
    )
    simulate_top_n: int = Field(
        default=3,
        ge=0,
        le=10,
        description="How many top-ranked guides to simulate with edit_outcome.",
    )
    run_offtarget_search: bool = Field(
        default=False,
        description=(
            "If true, run BLAST-backed off-target search for the top candidate guides. "
            "This makes the workflow expensive and requires approval in the agent loop."
        ),
    )
    offtarget_top_n: int = Field(
        default=1,
        ge=1,
        le=5,
        description="How many top guides to run through find_offtargets when enabled.",
    )
    offtarget_database: str = Field(
        default="nt",
        max_length=64,
        description="Database passed to find_offtargets / BLAST.",
    )
    max_offtarget_hits: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum off-target hits retained per searched guide.",
    )

    @field_validator("target")
    @classmethod
    def _validate_target(cls, value: str) -> str:
        cleaned = "".join(value.split()).upper()
        bad = set(cleaned) - {c.upper() for c in _DNA_CHARS}
        if bad:
            raise ValueError(f"target contains non-DNA characters: {sorted(bad)!r}")
        return cleaned


class OutcomeSummary(BaseModel):
    cut_position_fwd: int
    frameshift_probability: float = Field(
        description="Sum of probabilities for outcomes whose indel_size is not divisible by 3."
    )
    no_edit_probability: float
    top_outcomes: list[dict]


class OfftargetSummary(BaseModel):
    searched: bool
    database: str | None = None
    high_risk_count: int = 0
    medium_risk_count: int = 0
    low_risk_count: int = 0
    top_hits: list[dict] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class GuideReport(BaseModel):
    rank: int
    protospacer: str
    pam_sequence: str
    strand: Literal["+", "-"]
    protospacer_start: int
    protospacer_end: int
    pam_start: int
    pam_end: int
    heuristic_score: float
    on_target_score: float | None
    recommendation_score: float
    recommendation_label: Literal["preferred", "acceptable", "caution", "avoid"]
    rationale: list[str]
    off_target_summary: OfftargetSummary
    edit_outcome_summary: OutcomeSummary | None = None


class CrisprEditReportOutput(ToolOutput):
    target_length: int
    target_sequence: str = Field(
        description=(
            "The submitted target locus (the caller's own input, echoed back, cleaned + "
            "uppercased). Carried so a genome-browser view can render the target as its own "
            "reference and place each guide's protospacer/PAM at the forward-strand coordinates "
            "below. These coordinates are sequence-relative to THIS string only -- not a genome "
            "build; the tool never captures where this locus sits on a chromosome."
        )
    )
    pam: str
    num_guides_considered: int
    recommended_guide: GuideReport | None
    guides: list[GuideReport]
    tool_chain: list[str]
    caveats: list[str]


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _label(score: float, off: OfftargetSummary) -> Literal["preferred", "acceptable", "caution", "avoid"]:
    if off.high_risk_count >= 2:
        return "avoid"
    if off.high_risk_count == 1:
        return "caution"
    if score >= 0.8:
        return "preferred"
    if score >= 0.6:
        return "acceptable"
    return "caution"


def _rank_score(
    *,
    heuristic_score: float,
    on_target_score: float | None,
    off_target_summary: OfftargetSummary,
) -> float:
    base = (0.7 * on_target_score + 0.3 * heuristic_score) if on_target_score is not None else heuristic_score
    penalty = (
        0.25 * off_target_summary.high_risk_count
        + 0.08 * off_target_summary.medium_risk_count
        + 0.02 * off_target_summary.low_risk_count
    )
    return round(_clip01(base - penalty), 4)


def _summarize_edit_outcome(raw: dict) -> OutcomeSummary:
    outcomes = raw["outcomes"]
    frameshift_probability = sum(o["probability"] for o in outcomes if o["frameshift"])
    no_edit_probability = sum(o["probability"] for o in outcomes if o["outcome_type"] == "no_edit")
    return OutcomeSummary(
        cut_position_fwd=raw["cut_position_fwd"],
        frameshift_probability=round(frameshift_probability, 4),
        no_edit_probability=round(no_edit_probability, 4),
        top_outcomes=outcomes[:5],
    )


def _rationale(
    *,
    heuristic_score: float,
    on_target_score: float | None,
    off_target_summary: OfftargetSummary,
    outcome: OutcomeSummary | None,
) -> list[str]:
    parts: list[str] = []
    if on_target_score is not None:
        parts.append(f"On-target rule score {on_target_score:.2f}.")
    parts.append(f"Heuristic design score {heuristic_score:.2f}.")
    if off_target_summary.searched:
        parts.append(
            "Off-target screen found "
            f"{off_target_summary.high_risk_count} high, "
            f"{off_target_summary.medium_risk_count} medium, "
            f"{off_target_summary.low_risk_count} low risk candidates."
        )
    else:
        parts.append("Off-target search was not run.")
    if outcome is not None:
        parts.append(
            f"Deterministic NHEJ model estimates {outcome.frameshift_probability:.0%} "
            "aggregate frameshift probability among enumerated outcomes."
        )
    return parts


@register_tool(
    name="crispr_edit_report",
    description=(
        "Produce an end-to-end CRISPR edit-design report for a DNA target: design Cas9 "
        "guides, score candidates, optionally run BLAST-backed off-target search, "
        "simulate NHEJ edit outcomes, and recommend the best guide with rationale. "
        "Use this for high-level requests like 'design a CRISPR knockout experiment' "
        "or 'pick the best guide and tell me expected edit outcomes'. EXPENSIVE when "
        "`run_offtarget_search=true` because it calls `find_offtargets` / BLAST."
    ),
    input_model=CrisprEditReportInput,
    output_model=CrisprEditReportOutput,
    version="1.0.0",
    citations=[
        "Composes BioForge tools: design_guides, score_guide_on_target, edit_outcome, find_offtargets.",
        "Doench JG et al. (2014) Rational design of highly active sgRNAs. Nat Biotechnol 32:1262-1267.",
        "Hsu PD et al. (2013) DNA targeting specificity of RNA-guided Cas9 nucleases. Nat Biotechnol 31:827-832.",
        "van Overbeek M et al. (2016) DNA repair profiling reveals nonrandom outcomes at Cas9-mediated breaks. Mol Cell 63:633-646.",
    ],
    cost_hint="expensive",
    destructive=False,
    tags=["sequence", "crispr", "editing", "workflow", "report"],
    # Composite: may BLAST-search off-targets when run_offtarget_search=True. Scoring/uncertainty
    # metadata lives on the composed tools (design_guides / find_offtargets / edit_outcome).
    reference_data_keys=["ncbi_blast"],
)
async def crispr_edit_report(inp: CrisprEditReportInput) -> CrisprEditReportOutput:
    design = await execute_tool(
        "design_guides",
        {
            "sequence": inp.target,
            "pam": inp.pam,
            "max_guides": inp.max_guides,
            "compute_on_target_score": True,
        },
    )
    design_dict = design.model_dump()
    guides = design_dict["guides"]
    if not guides:
        return CrisprEditReportOutput(
            target_length=len(inp.target),
            target_sequence=inp.target,
            pam=inp.pam,
            num_guides_considered=0,
            recommended_guide=None,
            guides=[],
            tool_chain=["design_guides"],
            caveats=design_dict.get("notes", []),
        )

    tool_chain = ["design_guides", "score_guide_on_target"]
    off_targets_by_guide: dict[str, OfftargetSummary] = {}
    if inp.run_offtarget_search:
        tool_chain.append("find_offtargets")
        for guide in guides[: inp.offtarget_top_n]:
            raw = await execute_tool(
                "find_offtargets",
                {
                    "guide": guide["protospacer"],
                    "database": inp.offtarget_database,
                    "max_hits": inp.max_offtarget_hits,
                },
            )
            off = raw.model_dump()
            off_targets_by_guide[guide["protospacer"]] = OfftargetSummary(
                searched=True,
                database=off["database"],
                high_risk_count=off["high_risk_count"],
                medium_risk_count=off["medium_risk_count"],
                low_risk_count=off["low_risk_count"],
                top_hits=off["hits"][:5],
                caveats=off["caveats"],
            )

    tool_chain.append("edit_outcome")
    outcomes_by_guide: dict[str, OutcomeSummary] = {}
    for guide in guides[: inp.simulate_top_n]:
        try:
            raw = await execute_tool(
                "edit_outcome",
                {
                    "target": inp.target,
                    "guide": guide["protospacer"],
                    "pam": inp.pam,
                },
            )
            outcomes_by_guide[guide["protospacer"]] = _summarize_edit_outcome(raw.model_dump())
        except ToolError:
            # Keep the report useful if one guide cannot be localized unambiguously.
            continue

    report_guides: list[GuideReport] = []
    for guide in guides:
        off = off_targets_by_guide.get(
            guide["protospacer"],
            OfftargetSummary(searched=False),
        )
        outcome = outcomes_by_guide.get(guide["protospacer"])
        heuristic = guide["score"]["heuristic_score"]
        on_target = guide.get("on_target_score")
        recommendation_score = _rank_score(
            heuristic_score=heuristic,
            on_target_score=on_target,
            off_target_summary=off,
        )
        if inp.run_offtarget_search and not off.searched:
            # If the user requested specificity analysis, an unsearched guide should
            # never outrank a searched one just because we capped the number of BLAST
            # calls. Keep it visible, but do not recommend it as preferred.
            recommendation_score = min(recommendation_score, 0.5)
        report_guides.append(
            GuideReport(
                rank=0,
                protospacer=guide["protospacer"],
                pam_sequence=guide["pam_sequence"],
                strand=guide["strand"],
                protospacer_start=guide["protospacer_start"],
                protospacer_end=guide["protospacer_end"],
                pam_start=guide["pam_start"],
                pam_end=guide["pam_end"],
                heuristic_score=heuristic,
                on_target_score=on_target,
                recommendation_score=recommendation_score,
                recommendation_label=_label(recommendation_score, off),
                rationale=_rationale(
                    heuristic_score=heuristic,
                    on_target_score=on_target,
                    off_target_summary=off,
                    outcome=outcome,
                ),
                off_target_summary=off,
                edit_outcome_summary=outcome,
            )
        )

    if inp.run_offtarget_search:
        report_guides.sort(
            key=lambda g: (g.off_target_summary.searched, g.recommendation_score),
            reverse=True,
        )
    else:
        report_guides.sort(key=lambda g: g.recommendation_score, reverse=True)
    for idx, guide in enumerate(report_guides, start=1):
        guide.rank = idx

    caveats = [
        "This report composes multiple BioForge tools; inspect the trace for exact inputs and outputs.",
        "On-target scoring is rule-based, not the Doench Rule Set 2 trained model.",
        "Edit-outcome probabilities are published averages, not per-guide predictions.",
    ]
    if not inp.run_offtarget_search:
        caveats.append(
            "Off-target search was not run. For experiment planning, rerun with "
            "`run_offtarget_search=true` before choosing a guide."
        )
    else:
        caveats.append("Off-target search uses BLAST sequence matches and does not yet verify PAM context at each hit.")

    return CrisprEditReportOutput(
        target_length=len(inp.target),
        target_sequence=inp.target,
        pam=inp.pam,
        num_guides_considered=len(report_guides),
        recommended_guide=report_guides[0] if report_guides else None,
        guides=report_guides,
        tool_chain=tool_chain,
        caveats=caveats,
    )
