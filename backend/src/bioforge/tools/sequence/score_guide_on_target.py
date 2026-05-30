"""Rule-based on-target scoring for Cas9 guide RNAs.

# What this is

Combines published Cas9 design rules into a single 0-1 score per guide. The
contributing features come from:
  - Doench JG et al. (2014) Rational design of highly active sgRNAs for CRISPR-Cas9-
    mediated gene inactivation. Nat Biotechnol 32:1262-1267
  - Doench JG et al. (2016) Optimized sgRNA design to maximize activity and minimize
    off-target effects of CRISPR-Cas9. Nat Biotechnol 34:184-191
  - Liu G et al. (2020) Computational approaches for effective CRISPR guide RNA design
    and evaluation. Comput Struct Biotechnol J 18:35-44 (review of design rules)

# What this is NOT

This is NOT the Doench 2016 Rule Set 2 trained linear-regression model (often called
"Azimuth"). That model has ~500 features and trained coefficients fitted on a screen of
~2000 guides. Implementing it faithfully requires the published coefficients (in the
2016 paper's supplementary data) and a careful feature encoder. That's its own slice.

The score this tool emits is a transparent rule-based proxy. It is correlated with
on-target activity but does NOT claim Rule Set 2 fidelity. The agent's responder is
instructed (via tool description + caveats) to surface this distinction to the user.

# Why the rule-based proxy is still useful

  - Deterministic and inspectable — no opaque ML weights to argue about
  - Fast and dependency-free (no model file, no inference)
  - Captures the most-cited design preferences that account for the majority of the
    variance Rule Set 2 explains
  - Provides a baseline ranking that Rule Set 2 integration can supersede later
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

_DNA_CHARS = set("ACGT")
_GUIDE_LENGTH = 20  # Canonical SpCas9 protospacer length


# --- Position-specific nucleotide preferences ---------------------------------------
#
# Encodes the well-cited finding (Doench 2014 Fig 4, Table S1; corroborated in 2016 and
# in multiple downstream tools): in the PAM-proximal seed region (positions 16-20 of
# the 20-nt protospacer, 1-indexed), G and C are favored; position 20 in particular
# strongly favors G. In the PAM-distal region (positions 1-10), T is mildly disfavored.
#
# Weights are LITERATURE-DOCUMENTED relative preferences — not coefficients from a
# trained model. They sum to a feature-component score in [0, 1] after normalization.

# (position 1-indexed, base) → small weight contribution
_POSITION_PREFERENCES: dict[tuple[int, str], float] = {
    # Position 20 (just before PAM) — strongest preference
    (20, "G"): 0.30,
    (20, "C"): 0.15,
    (20, "A"): 0.05,
    (20, "T"): 0.00,
    # Position 19 — strong G/C preference
    (19, "G"): 0.18,
    (19, "C"): 0.12,
    (19, "A"): 0.05,
    (19, "T"): 0.00,
    # Position 18 — moderate G/C preference
    (18, "G"): 0.12,
    (18, "C"): 0.10,
    (18, "A"): 0.05,
    (18, "T"): 0.02,
    # Position 17 — mild G/C preference
    (17, "G"): 0.08,
    (17, "C"): 0.08,
    (17, "A"): 0.04,
    (17, "T"): 0.02,
    # Position 16 — mild G/C preference
    (16, "G"): 0.06,
    (16, "C"): 0.06,
    (16, "A"): 0.04,
    (16, "T"): 0.03,
    # Positions 1-10 — T disfavored
    (1, "T"): -0.02,
    (2, "T"): -0.02,
    (3, "T"): -0.02,
    (4, "T"): -0.01,
    (5, "T"): -0.01,
}

# Theoretical max position score (sum of the highest-weighted base at each scored position)
_POSITION_MAX = sum(
    max(v for (p, _), v in _POSITION_PREFERENCES.items() if p == pos)
    for pos in {p for p, _ in _POSITION_PREFERENCES.keys()}
)


# --- Dinucleotide preferences -------------------------------------------------------
#
# Doench 2014 Table S2 reports dinucleotide preferences at specific positions. We
# encode only the strongest, most-cited:
#   - GG at positions 19-20 is highly favored (independent of single-position weight)
#   - TT and AT in the 5' region are disfavored

_DINUCLEOTIDE_PREFERENCES: dict[tuple[int, str], float] = {
    (19, "GG"): 0.20,  # the "GG before NGG" preference
    (19, "GC"): 0.10,
    (19, "CG"): 0.05,
    (1, "TT"): -0.05,
    (1, "AT"): -0.03,
}

_DINUCLEOTIDE_MAX = sum(
    max(v for (p, _), v in _DINUCLEOTIDE_PREFERENCES.items() if p == pos)
    for pos in {p for p, _ in _DINUCLEOTIDE_PREFERENCES.keys()}
    if any(v > 0 for (q, _), v in _DINUCLEOTIDE_PREFERENCES.items() if q == pos)
)


# --- Input / output schemas ---------------------------------------------------------


class ScoreGuideOnTargetInput(ToolInput):
    protospacer: str = Field(
        ...,
        min_length=_GUIDE_LENGTH,
        max_length=_GUIDE_LENGTH,
        description=(
            "Exactly 20-nt SpCas9 protospacer (5'→3', DNA bases). PAM is NOT included. "
            "Truncated guides (<20 nt) are not supported by this score — the position-"
            "specific weights assume a 20-nt frame."
        ),
    )
    pam: str = Field(
        default="",
        max_length=4,
        description=(
            "Optional matched PAM (e.g. 'AGG'). Used only to record context — does not "
            "currently affect the score. Pass when available so the agent can quote it."
        ),
    )
    upstream_context: str = Field(
        default="",
        max_length=10,
        description=(
            "Optional 4-nt context immediately 5' of the protospacer. Not currently "
            "consumed by the score but accepted for forward compatibility with the "
            "Rule Set 2 model (which uses positions -4..+6 relative to protospacer)."
        ),
    )
    thirtymer: str = Field(
        default="",
        max_length=30,
        description=(
            "The 30-nt Azimuth / Rule Set 2 context window, REQUIRED when model='azimuth_rs2': "
            "4 nt 5' context + 20 nt protospacer + 3 nt PAM (NGG) + 3 nt 3' context, 5'→3'. The "
            "protospacer (positions 4-24) must equal `protospacer`. Rule Set 2 is a 30-mer model; "
            "a bare protospacer cannot be scored faithfully. Ignored for other models."
        ),
    )
    model: Literal["rule_based", "deepcrispr", "azimuth_rs2"] = Field(
        default="rule_based",
        description=(
            "Which on-target scorer to run. 'rule_based' (default) returns ONLY the "
            "transparent Doench-rules proxy — fast, deterministic, no setup. 'deepcrispr' "
            "ALSO runs the DeepCRISPR sequence-only CNN regression model (Chuai 2018, "
            "Apache-2.0) out-of-process and returns it side-by-side in "
            "`deepcrispr_on_target_score`; it REQUIRES a concrete 3-nt ACGT `pam` (the model "
            "scores the 23-bp protospacer+PAM window). 'azimuth_rs2' ALSO runs Doench 2016 "
            "Rule Set 2 (Azimuth, BSD-3-Clause) as the labeled SECONDARY scorer in "
            "`azimuth_rs2_on_target_score`; it REQUIRES the 30-nt `thirtymer` context window. "
            "Both deep modes need their legacy env built + enabled; if unavailable, the "
            "rule-based score is still returned, with a caveat."
        ),
    )

    @field_validator("protospacer")
    @classmethod
    def _validate_dna(cls, v: str) -> str:
        cleaned = "".join(v.split()).upper()
        bad = set(cleaned) - _DNA_CHARS
        if bad:
            raise ValueError(
                f"protospacer contains non-DNA characters: {sorted(bad)!r}. "
                "Ambiguous bases (N) are not supported by the position-specific scoring."
            )
        return cleaned

    @field_validator("upstream_context", "pam", "thirtymer")
    @classmethod
    def _validate_dna_optional(cls, v: str) -> str:
        cleaned = "".join(v.split()).upper()
        if cleaned:
            bad = set(cleaned) - _DNA_CHARS - {"N"}
            if bad:
                raise ValueError(f"contains non-DNA characters: {sorted(bad)!r}")
        return cleaned


class ScoreBreakdown(BaseModel):
    gc_component: float = Field(description="0-1: GC% in optimal [40, 60] range.")
    polyt_component: float = Field(description="0-1: no >= 4 consecutive T's.")
    position_component: float = Field(
        description=("0-1: normalized sum of position-specific nucleotide preferences from Doench 2014 Table S1.")
    )
    dinucleotide_component: float = Field(
        description=(
            "0-1: normalized sum of dinucleotide preferences (e.g. GG at positions 19-20). Doench 2014 Table S2."
        )
    )
    component_weights: dict[str, float] = Field(
        description="The weights applied to each component to form on_target_score."
    )


class ScoreGuideOnTargetOutput(ToolOutput):
    protospacer: str
    pam: str
    on_target_score: float = Field(
        description=(
            "Rule-based on-target activity score on [0, 1]. Higher = predicted higher "
            "Cas9 cleavage efficiency. NOT a Rule Set 2 ML prediction."
        )
    )
    score_breakdown: ScoreBreakdown
    deepcrispr_on_target_score: float | None = Field(
        default=None,
        description=(
            "DeepCRISPR sequence-only CNN regression on-target score (Chuai 2018), populated "
            "only when model='deepcrispr' AND the legacy environment is available; None "
            "otherwise. Shown SIDE-BY-SIDE with on_target_score — they use different scales, "
            "so compare guide rankings, not absolute values. Not yet validated end-to-end "
            "(see the DeepCRISPR legacy README)."
        ),
    )
    deepcrispr_model_version: str | None = Field(
        default=None,
        description="Provenance tag (DeepCRISPR model id + pinned upstream commit), when its score is present.",
    )
    azimuth_rs2_on_target_score: float | None = Field(
        default=None,
        description=(
            "Doench 2016 Rule Set 2 (Azimuth) on-target efficiency score, populated only when "
            "model='azimuth_rs2' AND the legacy environment is available; None otherwise. The "
            "labeled SECONDARY scorer, shown SIDE-BY-SIDE with on_target_score — different "
            "scales, so compare guide rankings, not absolute values. Never the primary scorer."
        ),
    )
    azimuth_rs2_model_version: str | None = Field(
        default=None,
        description="Provenance tag (Azimuth model id + pinned upstream commit), when its score is present.",
    )
    caveats: list[str]


# --- Scoring helpers ----------------------------------------------------------------


def _gc_component(seq: str) -> float:
    gc_pct = 100.0 * (seq.count("G") + seq.count("C")) / len(seq)
    if 40 <= gc_pct <= 60:
        return 1.0
    if gc_pct < 40:
        return max(0.0, (gc_pct - 20) / 20)
    return max(0.0, (80 - gc_pct) / 20)


def _polyt_component(seq: str) -> float:
    """1.0 when there's no run of ≥4 T's anywhere in the protospacer."""
    longest_t_run = 0
    current = 0
    for base in seq:
        if base == "T":
            current += 1
            longest_t_run = max(longest_t_run, current)
        else:
            current = 0
    return 1.0 if longest_t_run < 4 else 0.0


def _position_component(seq: str) -> float:
    """Sum position-specific preferences, normalize to [0, 1]."""
    raw = 0.0
    for i, base in enumerate(seq, start=1):
        raw += _POSITION_PREFERENCES.get((i, base), 0.0)
    if _POSITION_MAX <= 0:
        return 0.0
    # Normalize: raw=0 → 0.5 (neutral); raw=max → 1.0; raw negative → < 0.5
    normalized = 0.5 + (raw / (2 * _POSITION_MAX))
    return max(0.0, min(1.0, normalized))


def _dinucleotide_component(seq: str) -> float:
    raw = 0.0
    for i in range(len(seq) - 1):
        pos = i + 1  # 1-indexed
        dinuc = seq[i : i + 2]
        raw += _DINUCLEOTIDE_PREFERENCES.get((pos, dinuc), 0.0)
    if _DINUCLEOTIDE_MAX <= 0:
        return 0.5
    normalized = 0.5 + (raw / (2 * _DINUCLEOTIDE_MAX))
    return max(0.0, min(1.0, normalized))


# Weighting of components into the final score. Sum to 1.0.
_COMPONENT_WEIGHTS: dict[str, float] = {
    "gc": 0.30,
    "polyt": 0.20,
    "position": 0.35,
    "dinucleotide": 0.15,
}


def _score_with_deepcrispr(protospacer: str, pam: str) -> tuple[float | None, str | None, list[str]]:
    """Run the DeepCRISPR seq-only model for one guide, side-by-side with the rule-based score.

    Returns `(score, model_version, caveats)`. On a graceful unavailable/inference failure it
    returns `(None, None, [explanatory caveat])` so the rule-based score is still delivered.
    Raises `ToolError` only for an invalid PAM — a fixable input problem the agent should retry.
    """
    pam_clean = "".join(pam.split()).upper()
    if len(pam_clean) != 3 or (set(pam_clean) - set("ACGT")):
        raise ToolError(
            "model='deepcrispr' needs a concrete 3-nt ACGT pam (e.g. 'AGG') so the 23-bp "
            f"protospacer+PAM window can be formed; got pam={pam!r}. Pass the matched PAM from "
            "design_guides, or use model='rule_based'."
        )
    guide23 = protospacer + pam_clean

    # Local import: keeps registry load light and avoids importing the model glue unless used.
    from bioforge.tools.sequence.models.deepcrispr import (
        DeepCRISPRInferenceError,
        DeepCRISPRUnavailable,
        predict_on_target,
    )

    try:
        result = predict_on_target([guide23])
    except DeepCRISPRUnavailable as e:
        return (
            None,
            None,
            [
                f"DeepCRISPR on-target score unavailable: {e} The transparent rule-based on_target_score is returned alone."
            ],
        )
    except DeepCRISPRInferenceError as e:
        return None, None, [f"DeepCRISPR inference failed for this guide: {e} Returning the rule-based score only."]

    caveats = [
        "deepcrispr_on_target_score is the DeepCRISPR sequence-only CNN regression prediction "
        "(Chuai 2018, Apache-2.0), shown SIDE-BY-SIDE with the rule-based on_target_score. The "
        "two use different scales — compare guide RANKINGS, not absolute values.",
        "DeepCRISPR was trained on human cell-line data (HCT116, HEK293T, HeLa, HL60; Chuai "
        "2018). Sequences far from that distribution, or non-human systems, are out-of-"
        "distribution (§6) — treat the score with caution there.",
    ]
    return result.scores[0].score, result.model_version, caveats


def _score_with_azimuth_rs2(protospacer: str, thirtymer: str) -> tuple[float | None, str | None, list[str]]:
    """Run Azimuth / Doench Rule Set 2 for one guide, side-by-side with the rule-based score.

    Rule Set 2 scores a 30-nt window, not a bare protospacer, so `thirtymer` is REQUIRED and
    must carry the REAL genomic context — fabricating flanks would violate "AI never fabricates
    biology". Returns `(score, model_version, caveats)`; on a graceful unavailable/inference
    failure returns `(None, None, [caveat])` so the rule-based score is still delivered. Raises
    `ToolError` only for a fixable input problem (missing/short/inconsistent 30-mer).
    """
    from bioforge.tools.sequence.models.azimuth.manifest import PROTOSPACER_OFFSET, THIRTYMER_LENGTH

    cleaned = "".join(thirtymer.split()).upper()
    if not cleaned:
        raise ToolError(
            "model='azimuth_rs2' needs a 30-nt context window in `thirtymer` (4 nt 5' context + "
            "20 nt protospacer + 3 nt PAM + 3 nt 3' context). Rule Set 2 is a 30-mer model; a bare "
            "20-nt protospacer cannot be scored faithfully. Pass the real flanking context (e.g. "
            "from design_guides), or use model='rule_based'."
        )
    if len(cleaned) != THIRTYMER_LENGTH or (set(cleaned) - _DNA_CHARS):
        raise ToolError(
            f"model='azimuth_rs2' needs a {THIRTYMER_LENGTH}-nt ACGT window; got thirtymer={thirtymer!r} "
            f"({len(cleaned)} nt after cleanup)."
        )
    embedded = cleaned[PROTOSPACER_OFFSET : PROTOSPACER_OFFSET + _GUIDE_LENGTH]
    if embedded != protospacer:
        raise ToolError(
            "thirtymer is inconsistent with protospacer: expected the 20-nt protospacer at "
            f"positions {PROTOSPACER_OFFSET}-{PROTOSPACER_OFFSET + _GUIDE_LENGTH} of the 30-mer "
            f"(got {embedded!r}) to equal protospacer ({protospacer!r}). Check the window layout: "
            "4 nt 5' context + protospacer + 3 nt PAM + 3 nt 3' context."
        )

    # Local import: keeps registry load light and avoids importing the model glue unless used.
    from bioforge.tools.sequence.models.azimuth import (
        AzimuthInferenceError,
        AzimuthUnavailable,
        predict_on_target,
    )

    try:
        result = predict_on_target([cleaned])
    except AzimuthUnavailable as e:
        return (
            None,
            None,
            [
                f"Azimuth / Rule Set 2 on-target score unavailable: {e} The transparent rule-based "
                "on_target_score is returned alone."
            ],
        )
    except AzimuthInferenceError as e:
        return (
            None,
            None,
            [f"Azimuth / Rule Set 2 inference failed for this guide: {e} Returning the rule-based score only."],
        )

    caveats = [
        "azimuth_rs2_on_target_score is Doench 2016 Rule Set 2 (Azimuth, BSD-3-Clause), the "
        "labeled SECONDARY on-target scorer shown SIDE-BY-SIDE with the rule-based on_target_score "
        "for comparison / legacy reproducibility — never the sole or primary scorer. The two use "
        "different scales; compare guide RANKINGS, not absolute values.",
        "Rule Set 2 was trained on the Doench 2016 human-cell guide-efficiency screen "
        "(V3_model_nopos, sequence-only). Inputs far from that distribution are out-of-"
        "distribution (§6) — treat the score with caution there.",
    ]
    return result.scores[0].score, result.model_version, caveats


@register_tool(
    name="score_guide_on_target",
    description=(
        "Score a Cas9 guide RNA's predicted on-target activity using PUBLISHED design "
        "rules (Doench 2014/2016): GC content, polyT runs, position-specific "
        "nucleotide preferences (G/C in the PAM-proximal seed), and dinucleotide "
        "preferences (GG at positions 19-20). Returns a 0-1 score with a transparent "
        "breakdown of contributing components. Use after `design_guides` to refine the "
        "ranking, or whenever the user asks 'how good is this guide?'. IMPORTANT: this "
        "is NOT the Doench 2016 Rule Set 2 trained model — it's a rule-based proxy. "
        "Set model='deepcrispr' to ALSO run the DeepCRISPR sequence-only CNN model "
        "(Chuai 2018, Apache-2.0) side-by-side in `deepcrispr_on_target_score` (opt-in; "
        "needs a 3-nt PAM and the legacy env). Set model='azimuth_rs2' to ALSO run Doench "
        "2016 Rule Set 2 (Azimuth, BSD-3-Clause) as the labeled SECONDARY scorer in "
        "`azimuth_rs2_on_target_score` (opt-in; needs the 30-nt `thirtymer` context window "
        "and the legacy env). "
        "The output field is named `on_target_score`, not `doench_score`, to keep that "
        "distinction visible."
    ),
    input_model=ScoreGuideOnTargetInput,
    output_model=ScoreGuideOnTargetOutput,
    version="1.0.0",
    citations=[
        "Doench JG et al. (2014) Rational design of highly active sgRNAs for CRISPR-Cas9-mediated gene inactivation. Nat Biotechnol 32:1262-1267",
        "Doench JG et al. (2016) Optimized sgRNA design to maximize activity and minimize off-target effects of CRISPR-Cas9. Nat Biotechnol 34:184-191",
        "Liu G et al. (2020) Computational approaches for effective CRISPR guide RNA design and evaluation. Comput Struct Biotechnol J 18:35-44 (design rule review)",
    ],
    cost_hint="cheap",
    destructive=False,
    tags=["sequence", "crispr", "scoring"],
    model_versions={
        "on_target": "bioforge-rule-based-proxy-1.0.0",
        "deepcrispr": "deepcrispr-ontar-cnn-reg-seq",
        "azimuth_rs2": "azimuth-doench-rule-set-2-v3-nopos",
    },
    emits_instance_uncertainty={"on_target": False, "deepcrispr": False, "azimuth_rs2": False},
    published_accuracy={
        "on_target": (
            "VERIFY: transparent rule-based proxy of Doench 2014/2016 features; NOT the trained "
            "Rule Set 2 (Azimuth) model, so no standalone published held-out accuracy applies."
        ),
        "deepcrispr": (
            "VERIFY: DeepCRISPR (Chuai 2018, Genome Biol 19:80) reports on-target ROC-AUC 0.857 "
            "(classification); the seq-only regression Spearman is in Additional file 3 — confirm "
            "the exact figure at numeric validation before displaying it as calibrated."
        ),
        "azimuth_rs2": (
            "VERIFY: Doench 2016 Rule Set 2 (Azimuth) reports a Spearman correlation on held-out "
            "guide-efficiency data; confirm the exact value + dataset against Doench et al. 2016 "
            "(Nat Biotechnol 34:184-191) before displaying it as calibrated."
        ),
    },
    training_distribution={
        "guide_length_nt": 20,
        "note": "rule-based heuristic, not a trained model",
        "deepcrispr_note": (
            "model='deepcrispr' uses DeepCRISPR, trained on human cell-line data "
            "(HCT116, HEK293T, HeLa, HL60; Chuai 2018) — declare that as its OOD envelope."
        ),
        "azimuth_rs2_note": (
            "model='azimuth_rs2' uses Doench 2016 Rule Set 2 (Azimuth), trained on a human-cell "
            "guide-efficiency screen (Doench 2016); needs a 30-nt context window — declare that "
            "as its OOD envelope."
        ),
    },
    reference_data_keys=["deepcrispr_weights", "azimuth_weights"],
)
async def score_guide_on_target(
    inp: ScoreGuideOnTargetInput,
) -> ScoreGuideOnTargetOutput:
    seq = inp.protospacer
    if len(seq) != _GUIDE_LENGTH:
        # Defensive: validator should already enforce this.
        raise ToolError(
            f"score_guide_on_target requires a 20-nt protospacer; got {len(seq)} nt. "
            "Truncated guides are not supported by the position-specific scoring."
        )

    gc = _gc_component(seq)
    polyt = _polyt_component(seq)
    position = _position_component(seq)
    dinuc = _dinucleotide_component(seq)

    score = (
        _COMPONENT_WEIGHTS["gc"] * gc
        + _COMPONENT_WEIGHTS["polyt"] * polyt
        + _COMPONENT_WEIGHTS["position"] * position
        + _COMPONENT_WEIGHTS["dinucleotide"] * dinuc
    )

    caveats = [
        "on_target_score is a TRANSPARENT rule-based combination of published "
        "design preferences (Doench 2014/2016). It is NOT the Doench Rule Set 2 "
        "trained model — run that via model='azimuth_rs2' (Azimuth, opt-in).",
        "Position-specific weights are derived from Doench 2014 Tables S1/S2 "
        "qualitative preferences, normalized into [0,1]. Different downstream "
        "tools (CRISPRko, CRISPick) use different weightings of the same features.",
        "This score does NOT predict off-target activity — use `find_offtargets` for specificity assessment.",
    ]

    deepcrispr_score: float | None = None
    deepcrispr_version: str | None = None
    if inp.model == "deepcrispr":
        deepcrispr_score, deepcrispr_version, dc_caveats = _score_with_deepcrispr(seq, inp.pam)
        caveats.extend(dc_caveats)

    azimuth_rs2_score: float | None = None
    azimuth_rs2_version: str | None = None
    if inp.model == "azimuth_rs2":
        azimuth_rs2_score, azimuth_rs2_version, az_caveats = _score_with_azimuth_rs2(seq, inp.thirtymer)
        caveats.extend(az_caveats)

    return ScoreGuideOnTargetOutput(
        protospacer=seq,
        pam=inp.pam,
        on_target_score=round(score, 4),
        score_breakdown=ScoreBreakdown(
            gc_component=round(gc, 4),
            polyt_component=round(polyt, 4),
            position_component=round(position, 4),
            dinucleotide_component=round(dinuc, 4),
            component_weights=dict(_COMPONENT_WEIGHTS),
        ),
        deepcrispr_on_target_score=deepcrispr_score,
        deepcrispr_model_version=deepcrispr_version,
        azimuth_rs2_on_target_score=azimuth_rs2_score,
        azimuth_rs2_model_version=azimuth_rs2_version,
        caveats=caveats,
    )
