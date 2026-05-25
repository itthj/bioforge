"""CRISPR-Cas9 guide RNA design — first cut.

Scans a target DNA sequence on both strands for SpCas9 PAM motifs (NGG by default) and
extracts the 20-nt protospacer immediately 5' of each PAM. Each candidate guide is
annotated with design metrics that have well-established support in the literature
(GC%, polyT, mononucleotide-run length, simple self-complementarity).

What this tool does NOT do (yet, by design):
  - Doench 2016 Rule Set 2 on-target efficiency scoring. Implementing it requires a
    trained linear model with specific feature encodings — that's its own slice.
  - Off-target search. Compose with the existing `blast` tool against a reference genome
    using a `task=blastn-short` variant. A dedicated `find_offtargets` tool is a follow-up.
  - DeepCRISPR / CRISPick / other ML-based scoring.

The ranking score we DO compute is a transparent, deterministic heuristic — documented
in `HeuristicScore` — so the user can audit and override. It is NOT a substitute for
Doench-style on-target scoring; we mark it explicitly as `heuristic_score` (not
`on_target_score`) to avoid confusion in the trace and the agent's response.
"""

from __future__ import annotations

import re
from typing import Literal

from Bio.Seq import Seq
from pydantic import BaseModel, Field, field_validator

from bioforge.tools.base import ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

_DNA_CHARS = set("ACGTNacgtn")
_AMBIG_REGEX = {
    "A": "A",
    "C": "C",
    "G": "G",
    "T": "T",
    "N": "[ACGT]",
    "R": "[AG]",   # puRine
    "Y": "[CT]",   # pYrimidine
    "S": "[GC]",   # Strong (3 H-bonds)
    "W": "[AT]",   # Weak  (2 H-bonds)
    "K": "[GT]",   # Keto
    "M": "[AC]",   # aMino
    "B": "[CGT]",  # not A
    "D": "[AGT]",  # not C
    "H": "[ACT]",  # not G
    "V": "[ACG]",  # not T  ← Cas12a uses TTTV
}


def _pam_to_regex(pam: str) -> str:
    """Convert a PAM string with IUPAC ambiguity codes to a regex pattern."""
    return "".join(_AMBIG_REGEX[c.upper()] for c in pam)


class DesignGuidesInput(ToolInput):
    sequence: str = Field(
        ...,
        min_length=23,
        description=(
            "Target DNA sequence (the locus to edit). Must be long enough that a PAM "
            "found anywhere within it leaves room for a 20-nt protospacer upstream — "
            "so at least 23 nt for a 20-nt guide + 3-nt NGG PAM."
        ),
    )
    pam: str = Field(
        default="NGG",
        min_length=2,
        max_length=10,
        description=(
            "PAM motif using IUPAC codes (A/C/G/T/N/R/Y/S/W/K/M). Default 'NGG' is "
            "SpCas9. Use 'TTTV' for Cas12a, 'NGA' for relaxed-PAM SpCas9 variants, etc."
        ),
    )
    guide_length: int = Field(
        default=20,
        ge=15,
        le=25,
        description=(
            "Protospacer length in nucleotides. 20 is the canonical SpCas9 length; some "
            "applications use 17-19 nt truncated guides for improved specificity."
        ),
    )
    strands: list[Literal["+", "-"]] = Field(
        default_factory=lambda: ["+", "-"],
        description="Which strand(s) to scan. Default both.",
        min_length=1,
        max_length=2,
    )
    max_guides: int = Field(
        default=20,
        ge=1,
        le=200,
        description="Cap the response to the top N guides ranked by heuristic_score.",
    )
    compute_on_target_score: bool = Field(
        default=False,
        description=(
            "If true, also call `score_guide_on_target` for each candidate and surface "
            "the resulting `on_target_score` alongside `heuristic_score`. When this is "
            "enabled, candidates are ranked by `on_target_score` (with heuristic_score "
            "as tiebreaker) instead of by `heuristic_score` alone. Default false to "
            "keep the tool cheap and dependency-free unless the user explicitly wants "
            "the deeper analysis. Only applies when guide_length == 20."
        ),
    )

    @field_validator("sequence")
    @classmethod
    def _validate_dna(cls, v: str) -> str:
        cleaned = "".join(v.split()).upper()
        if not cleaned:
            raise ValueError("sequence is empty after stripping whitespace")
        bad = set(cleaned) - {c.upper() for c in _DNA_CHARS}
        if bad:
            raise ValueError(f"sequence contains non-DNA characters: {sorted(bad)!r}")
        return cleaned

    @field_validator("pam")
    @classmethod
    def _validate_pam(cls, v: str) -> str:
        cleaned = v.upper()
        bad = set(cleaned) - set(_AMBIG_REGEX)
        if bad:
            raise ValueError(
                f"PAM contains unsupported characters: {sorted(bad)!r}. "
                f"Supported IUPAC codes: {sorted(_AMBIG_REGEX)}"
            )
        return cleaned


class HeuristicScore(BaseModel):
    """Transparent breakdown of the design score. NOT a Doench-style ML prediction.

    Components (each a 0-1 sub-score, then weighted):
      - gc_score:        1.0 when GC% is in [40, 60]; falls off linearly to 0 at <=20 or >=80
      - polyt_score:     1.0 when no run of T >= 4; 0.0 when there is one
      - mononuc_score:   1.0 when no run of any base >= 5; 0.0 when there is one
      - selfcomp_score:  1.0 when no self-complementary subsequence >= 6 nt; 0.0 otherwise

    Final `heuristic_score` is the weighted sum (gc 0.4, polyt 0.3, mononuc 0.2,
    selfcomp 0.1) on [0, 1].
    """

    gc_score: float
    polyt_score: float
    mononuc_score: float
    selfcomp_score: float
    heuristic_score: float


class Guide(BaseModel):
    protospacer: str = Field(description="20-nt guide sequence (5'→3', on the target strand).")
    pam_sequence: str = Field(description="The matched PAM (e.g. 'AGG' for an NGG hit).")
    strand: Literal["+", "-"]
    # Coordinates on the FORWARD strand of the input, 0-based half-open.
    protospacer_start: int
    protospacer_end: int
    pam_start: int
    pam_end: int
    gc_percent: float
    longest_polyt: int = Field(description="Longest run of consecutive T's in the protospacer.")
    longest_mononuc_run: int = Field(
        description="Longest run of ANY single base in the protospacer."
    )
    self_complementarity_max: int = Field(
        description=(
            "Length of the longest substring of the protospacer that is also present in "
            "its reverse complement (a proxy for hairpin propensity)."
        )
    )
    score: HeuristicScore
    on_target_score: float | None = Field(
        default=None,
        description=(
            "Doench 2014/2016 rule-based on-target score on [0, 1], populated only when "
            "the caller passed `compute_on_target_score=True`. NOT a Rule Set 2 ML "
            "prediction — see the `score_guide_on_target` tool for the scoring details."
        ),
    )


class DesignGuidesOutput(ToolOutput):
    pam: str
    guide_length: int
    target_length: int
    num_candidates_total: int
    num_returned: int
    guides: list[Guide]
    notes: list[str] = Field(
        default_factory=list,
        description="Caveats the agent must surface to the user (e.g. heuristic-vs-Doench).",
    )


# --- Metric helpers ------------------------------------------------------------------


def _gc_percent(seq: str) -> float:
    if not seq:
        return 0.0
    return round(100.0 * (seq.count("G") + seq.count("C")) / len(seq), 2)


def _longest_run(seq: str, base: str | None = None) -> int:
    """Longest run of a specific base, or of any single base if base is None.

    When `base` is specified and not present, returns 0 (NOT 1). When `base` is None,
    returns the longest streak of identical adjacent characters (>= 1 for non-empty input).
    """
    if not seq:
        return 0
    if base is not None:
        best = 0
        current = 0
        for ch in seq:
            if ch == base:
                current += 1
                best = max(best, current)
            else:
                current = 0
        return best
    # base is None: longest streak of any identical adjacent characters
    best = 1
    current = 1
    for i in range(1, len(seq)):
        if seq[i] == seq[i - 1]:
            current += 1
            best = max(best, current)
        else:
            current = 1
    return best


def _longest_selfcomp(seq: str, min_len: int = 4) -> int:
    """Longest k-mer present in `seq` that's also present in its reverse complement.

    O(n^2) but n=20 so trivial. Returns the maximum match length found (>= min_len), or
    0 if no match of at least min_len exists.
    """
    rc = str(Seq(seq).reverse_complement())
    longest = 0
    n = len(seq)
    for i in range(n):
        for j in range(i + min_len, n + 1):
            kmer = seq[i:j]
            if kmer in rc:
                longest = max(longest, len(kmer))
    return longest


def _score(
    gc_pct: float, longest_t: int, longest_run: int, selfcomp: int
) -> HeuristicScore:
    # gc: 1.0 in [40,60], linear ramp down to 0 at <=20 or >=80
    if 40 <= gc_pct <= 60:
        gc_score = 1.0
    elif gc_pct < 40:
        gc_score = max(0.0, (gc_pct - 20) / 20)
    else:
        gc_score = max(0.0, (80 - gc_pct) / 20)
    polyt_score = 1.0 if longest_t < 4 else 0.0
    mononuc_score = 1.0 if longest_run < 5 else 0.0
    selfcomp_score = 1.0 if selfcomp < 6 else 0.0
    final = round(
        0.4 * gc_score
        + 0.3 * polyt_score
        + 0.2 * mononuc_score
        + 0.1 * selfcomp_score,
        4,
    )
    return HeuristicScore(
        gc_score=round(gc_score, 4),
        polyt_score=polyt_score,
        mononuc_score=mononuc_score,
        selfcomp_score=selfcomp_score,
        heuristic_score=final,
    )


# --- Scan ---------------------------------------------------------------------------


def _scan_strand(
    fwd_target: str, pam_pattern: str, guide_length: int, strand: Literal["+", "-"]
) -> list[Guide]:
    """Find all PAM hits on the chosen strand and emit candidate Guides.

    Coordinates returned are ALWAYS on the forward strand of the input target — for "-"
    strand hits we map back via `len(target) - end` / `len(target) - start`.
    """
    target_strand = fwd_target if strand == "+" else str(Seq(fwd_target).reverse_complement())
    n = len(target_strand)
    pam_re = re.compile(pam_pattern)
    guides: list[Guide] = []

    for m in pam_re.finditer(target_strand):
        pam_start = m.start()
        pam_end = m.end()
        protospacer_start = pam_start - guide_length
        if protospacer_start < 0:
            continue  # PAM too close to 5' end on this strand to have a full protospacer
        protospacer = target_strand[protospacer_start:pam_start]
        if "N" in protospacer:
            # Refuse to design against ambiguous bases — caller passed bad input.
            continue
        pam_sequence = target_strand[pam_start:pam_end]

        gc_pct = _gc_percent(protospacer)
        longest_t = _longest_run(protospacer, "T")
        longest_mn = _longest_run(protospacer)
        selfcomp = _longest_selfcomp(protospacer)
        score = _score(gc_pct, longest_t, longest_mn, selfcomp)

        if strand == "+":
            fwd_proto_start, fwd_proto_end = protospacer_start, pam_start
            fwd_pam_start, fwd_pam_end = pam_start, pam_end
        else:
            # Map coordinates back to forward strand.
            fwd_proto_start = n - pam_start
            fwd_proto_end = n - protospacer_start
            fwd_pam_start = n - pam_end
            fwd_pam_end = n - pam_start

        guides.append(
            Guide(
                protospacer=protospacer,
                pam_sequence=pam_sequence,
                strand=strand,
                protospacer_start=fwd_proto_start,
                protospacer_end=fwd_proto_end,
                pam_start=fwd_pam_start,
                pam_end=fwd_pam_end,
                gc_percent=gc_pct,
                longest_polyt=longest_t,
                longest_mononuc_run=longest_mn,
                self_complementarity_max=selfcomp,
                score=score,
            )
        )

    return guides


# --- Tool ---------------------------------------------------------------------------


@register_tool(
    name="design_guides",
    description=(
        "Design CRISPR-Cas9 guide RNAs for a target DNA sequence. Scans both strands "
        "for PAM motifs (default 'NGG' for SpCas9; configurable via IUPAC codes for "
        "Cas12a / variant Cas9s), extracts the 20-nt protospacer upstream of each, and "
        "ranks candidates with a TRANSPARENT design heuristic: GC content, polyT runs, "
        "mononucleotide runs, and basic self-complementarity. Returns ranked guides "
        "with forward-strand coordinates. Use when the user wants to edit, knock out, "
        "or modify a gene, or asks for sgRNAs / guide RNAs / Cas9 targeting. "
        "IMPORTANT: this tool does NOT compute Doench 2016 on-target scores or off-"
        "target hits — those are separate tools. The `heuristic_score` is a deterministic "
        "first-pass filter, not a substitute for proper on-target prediction."
    ),
    input_model=DesignGuidesInput,
    output_model=DesignGuidesOutput,
    version="1.0.0",
    citations=[
        "Jinek M et al. (2012) A programmable dual-RNA-guided DNA endonuclease in adaptive bacterial immunity. Science 337:816-821",
        "Cong L et al. (2013) Multiplex genome engineering using CRISPR/Cas systems. Science 339:819-823",
        "Design rules per: Doench JG et al. (2014) Rational design of highly active sgRNAs for CRISPR-Cas9-mediated gene inactivation. Nat Biotechnol 32:1262-1267 (GC content, polyT)",
    ],
    cost_hint="cheap",
    destructive=False,
    tags=["sequence", "crispr", "design"],
)
async def design_guides(inp: DesignGuidesInput) -> DesignGuidesOutput:
    pam_re = _pam_to_regex(inp.pam)
    guides: list[Guide] = []
    for strand in inp.strands:
        guides.extend(_scan_strand(inp.sequence, pam_re, inp.guide_length, strand))

    if not guides:
        return DesignGuidesOutput(
            pam=inp.pam,
            guide_length=inp.guide_length,
            target_length=len(inp.sequence),
            num_candidates_total=0,
            num_returned=0,
            guides=[],
            notes=[
                f"No {inp.pam} PAM sites with a full {inp.guide_length}-nt protospacer "
                "found on the requested strand(s). Try a relaxed PAM or extend the "
                "target sequence to include more flanking context."
            ],
        )

    # Optionally enrich each candidate with on_target_score. Only 20-nt protospacers
    # are supported by score_guide_on_target — for non-canonical guide lengths we skip
    # silently and emit a note explaining why.
    on_target_supported = inp.compute_on_target_score and inp.guide_length == 20
    if on_target_supported:
        # Import locally to avoid a circular dependency at registration time.
        from bioforge.tools.sequence.score_guide_on_target import (
            ScoreGuideOnTargetInput,
            score_guide_on_target,
        )

        for g in guides:
            try:
                scored = await score_guide_on_target(
                    ScoreGuideOnTargetInput(
                        protospacer=g.protospacer, pam=g.pam_sequence
                    )
                )
                g.on_target_score = scored.on_target_score
            except Exception:  # noqa: BLE001
                # If scoring fails for any individual guide (shouldn't happen given the
                # input came from this tool's own scanner), leave on_target_score=None
                # rather than aborting the whole call.
                g.on_target_score = None

    # Ranking: when on_target_score is available for ALL candidates, sort by that with
    # heuristic_score as tiebreaker. Otherwise fall back to heuristic_score alone.
    all_have_on_target = on_target_supported and all(
        g.on_target_score is not None for g in guides
    )
    if all_have_on_target:
        guides.sort(
            key=lambda g: (
                g.on_target_score if g.on_target_score is not None else 0.0,
                g.score.heuristic_score,
            ),
            reverse=True,
        )
    else:
        guides.sort(key=lambda g: g.score.heuristic_score, reverse=True)

    total = len(guides)
    guides = guides[: inp.max_guides]

    notes = [
        (
            "`heuristic_score` is a transparent first-pass filter (GC content, polyT, "
            "mononucleotide run, self-complementarity). It is NOT a Doench Rule Set 2 "
            "prediction — when you need more nuanced on-target scoring, pass "
            "`compute_on_target_score=True` or call `score_guide_on_target` directly."
        ),
        "Off-target sites are NOT searched here. Compose with `find_offtargets` "
        "against a reference database for specificity analysis.",
    ]
    if inp.compute_on_target_score and not on_target_supported:
        notes.append(
            f"`compute_on_target_score=True` ignored: on-target scoring is only "
            f"defined for 20-nt protospacers and you requested guide_length="
            f"{inp.guide_length}. Returned guides ranked by heuristic_score alone."
        )
    if all_have_on_target:
        notes.append(
            "Guides ranked by `on_target_score` (Doench 2014/2016 rule-based) with "
            "`heuristic_score` as tiebreaker. `on_target_score` populated on each "
            "guide. See `score_guide_on_target` for scoring details — this is NOT a "
            "Rule Set 2 ML prediction."
        )

    return DesignGuidesOutput(
        pam=inp.pam,
        guide_length=inp.guide_length,
        target_length=len(inp.sequence),
        num_candidates_total=total,
        num_returned=len(guides),
        guides=guides,
        notes=notes,
    )
