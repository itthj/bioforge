"""In-silico CRISPR edit-outcome prediction — deterministic NHEJ model.

Given a target DNA sequence and a guide RNA (protospacer), this tool:
  1. Locates the guide on either strand of the target
  2. Computes the Cas9 cut site (3 nt upstream of the PAM on the protospacer strand)
  3. Enumerates the common NHEJ repair outcomes: perfect repair, +1 insertions for each
     of A/C/G/T at the cut site, and -1 / -2 / -3 deletions centered on the cut
  4. Assigns rule-of-thumb probabilities derived from published NHEJ-frequency averages
     across a range of guides (NOT a per-guide prediction)
  5. Flags each outcome as frameshift / in-frame based on indel size modulo 3

# What this is, and what it is NOT

This is a DETERMINISTIC enumerator with PUBLISHED-AVERAGE probabilities. It is the
Phase 2 first cut. The probabilities are stable per outcome type and do NOT depend on
the guide sequence. They are placeholders that exist in the schema specifically so
guide-specific ML models (inDelphi, FORECasT, Lindel) can swap in for Phase 2.5
WITHOUT schema or downstream tool changes — the output shape is already
`outcomes: list[{edited_sequence, outcome_type, indel_size, probability, frameshift,
notes}]`.

The agent's responder is instructed (via the tool description + output `notes`) to
make this distinction visible to the user. Confidently presenting these
probabilities as predictions for a SPECIFIC guide is exactly the hallucination this
project's principles forbid.

# Things deliberately deferred to later slices

  - Microhomology-mediated end joining (MMEJ): scans for short repeats flanking the
    cut and biases probabilities toward MH-templated deletions. Real models include it.
  - Cas12a (staggered cuts at +18/+23 from PAM rather than blunt at -3): different cut
    geometry, separate tool variant later.
  - HDR with a donor template: completely different repair pathway, needs the donor
    sequence as input.
  - Guide-specific indel-distribution prediction: requires inDelphi / FORECasT /
    Lindel pretrained models (Phase 2.5).
"""

from __future__ import annotations

import re
from typing import Literal

from Bio.Seq import Seq
from pydantic import BaseModel, Field, field_validator

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool
from bioforge.tools.sequence.microhomology import (
    apply_mmej_deletion,
    find_microhomologies,
    normalize_to_probabilities,
)

_DNA_CHARS = set("ACGTNacgtn")
_IUPAC_REGEX = {
    "A": "A",
    "C": "C",
    "G": "G",
    "T": "T",
    "N": "[ACGT]",
    "R": "[AG]",
    "Y": "[CT]",
    "S": "[GC]",
    "W": "[AT]",
    "K": "[GT]",
    "M": "[AC]",
    "B": "[CGT]",
    "D": "[AGT]",
    "H": "[ACT]",
    "V": "[ACG]",
}


# --- Rule-of-thumb NHEJ probabilities (averages across literature) ------------------
#
# These are documented broad averages for SpCas9 NHEJ repair WITHOUT a donor template,
# averaged across many guides and cell types. Citations in the tool's `citations` field.
# They are NOT a prediction for any specific guide. Sum across enumerated outcomes ≈ 1.0.

_RULE_OF_THUMB_PROBS: dict[str, float] = {
    "no_edit": 0.50,  # perfect repair: dominant when no edit is selected for
    "insertion_+1_A": 0.07,
    "insertion_+1_C": 0.03,
    "insertion_+1_G": 0.03,
    "insertion_+1_T": 0.07,  # +1A/T biased over +1C/G (Cas9 microhomology bias)
    "deletion_-1": 0.10,
    "deletion_-2": 0.05,
    "deletion_-3": 0.05,
    "deletion_larger": 0.10,  # aggregated bucket for everything > 3-nt deletion
}


# --- Input / output models ----------------------------------------------------------


OutcomeType = Literal[
    "no_edit",
    "insertion_+1_A",
    "insertion_+1_C",
    "insertion_+1_G",
    "insertion_+1_T",
    "deletion_-1",
    "deletion_-2",
    "deletion_-3",
    "deletion_larger",
    "mmej_deletion",
]


# MMEJ accounts for ~20-50% of Cas9 repair events in published Cas9 datasets;
# 35% is a defensible middle value (van Overbeek 2016 / Shen 2018 averages).
# When MMEJ outcomes are generated, NHEJ probabilities are scaled by (1 - this).
_DEFAULT_MMEJ_FRACTION = 0.35


class EditOutcomeInput(ToolInput):
    target: str = Field(
        ...,
        min_length=30,
        description=(
            "Target DNA sequence (the locus you'd edit). Must extend at least ~20 nt on "
            "BOTH sides of the cut so deletion outcomes have flanking context. 30 nt is "
            "the bare minimum; 200+ nt is recommended."
        ),
    )
    guide: str = Field(
        ...,
        min_length=15,
        max_length=25,
        description=(
            "Guide RNA protospacer sequence (DNA bases — NOT U/T conversion). Typically "
            "20 nt for SpCas9. Must be findable in the target on at least one strand."
        ),
    )
    pam: str = Field(
        default="NGG",
        description=(
            "PAM motif (IUPAC codes) used to anchor the protospacer's 3' end. Default "
            "NGG for SpCas9. Cas12a / variant Cas9 PAMs are configurable but the cut "
            "geometry assumed here is SpCas9-style (3 nt upstream of PAM)."
        ),
        min_length=2,
        max_length=10,
    )
    cut_offset_from_pam: int = Field(
        default=3,
        ge=1,
        le=10,
        description=(
            "Distance from the 5' edge of the PAM at which Cas9 cleaves, on the "
            "protospacer strand. SpCas9 default is 3 (creating a blunt cut between "
            "positions -3 and -4 relative to PAM)."
        ),
    )
    include_outcome_types: list[OutcomeType] | None = Field(
        default=None,
        description=(
            "Restrict the enumeration to specific outcome types. Default (None) emits all 9 standard outcomes."
        ),
    )
    enable_mmej: bool = Field(
        default=True,
        description=(
            "Scan for microhomologies flanking the cut and emit mmej_deletion "
            "outcomes scored by the Bae 2014 algorithm. When True (default), "
            "NHEJ rule-of-thumb probabilities are scaled by (1 - mmej_total_fraction). "
            "Disable to revert to the pure-NHEJ first-cut behavior."
        ),
    )
    mmej_total_fraction: float = Field(
        default=_DEFAULT_MMEJ_FRACTION,
        ge=0.0,
        le=0.9,
        description=(
            "Fraction of total Cas9 repair events attributed to MMEJ. Published "
            "datasets put this at 20-50%; default 0.35 is a reasonable middle. "
            "Set to 0 to behave as if enable_mmej=False; set higher for cut sites "
            "in MH-rich contexts (CTG / CGG repeats, GC-rich promoters)."
        ),
    )
    min_microhomology_length: int = Field(
        default=2,
        ge=2,
        le=6,
        description=(
            "Minimum length of microhomology to consider. 2 is the published "
            "threshold and captures most MMEJ outcomes; raise to 3-4 to focus "
            "on the strongest patterns only."
        ),
    )

    @field_validator("target", "guide")
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
        bad = set(cleaned) - set(_IUPAC_REGEX)
        if bad:
            raise ValueError(
                f"PAM contains unsupported characters: {sorted(bad)!r}. Supported IUPAC codes: {sorted(_IUPAC_REGEX)}"
            )
        return cleaned


class MicrohomologyAnnotation(BaseModel):
    """For mmej_deletion outcomes, the MH pair that templated the deletion."""

    sequence: str
    length: int
    left_position: int = Field(description="0-based start of the LEFT MH copy on the forward strand.")
    right_position: int = Field(description="0-based start of the RIGHT MH copy on the forward strand.")
    pattern_score: float = Field(
        description=("Bae 2014 pattern score (raw, unnormalized). Higher = more likely this MH templates the deletion.")
    )


class EditOutcome(BaseModel):
    outcome_type: OutcomeType
    edited_sequence: str = Field(
        description=(
            "The full post-repair DNA on the forward strand of the input target. For "
            "deletions, length < target length; for insertions, length > target length; "
            "for no_edit, identical to target."
        )
    )
    indel_size: int = Field(
        description=(
            "Net length change: positive for insertions, negative for deletions, 0 for "
            "no_edit. Used for frameshift detection."
        )
    )
    probability: float = Field(
        description=(
            "Estimated probability. For mmej_deletion: derived from Bae 2014 "
            "pattern scoring + a 35% MMEJ-of-total budget. For NHEJ outcomes: "
            "published rule-of-thumb averages, scaled down by (1 - MMEJ total) "
            "when MMEJ outcomes are present. NOT a per-guide ML prediction."
        )
    )
    frameshift: bool = Field(
        description=(
            "True iff `indel_size % 3 != 0`. Note: this only indicates the edit will "
            "shift the reading frame IF the cut site falls within a CDS. The tool does "
            "not know whether your target is coding — you do."
        )
    )
    notes: str = Field(default="")
    microhomology: MicrohomologyAnnotation | None = Field(
        default=None,
        description=(
            "Populated for mmej_deletion outcomes; None for NHEJ outcomes. "
            "Identifies which MH pair flanking the cut produced this deletion."
        ),
    )


class EditOutcomeOutput(ToolOutput):
    guide: str
    guide_strand: Literal["+", "-"]
    cut_position_fwd: int = Field(description="0-based position on the forward strand where Cas9 cuts.")
    target_length: int
    outcomes: list[EditOutcome]
    summary_caveats: list[str] = Field(
        default_factory=list,
        description=(
            "Caveats the agent's responder is expected to surface to the user — most "
            "importantly that probabilities are published averages, not predictions."
        ),
    )


# --- Guide localization --------------------------------------------------------------


def _pam_to_regex(pam: str) -> str:
    return "".join(_IUPAC_REGEX[c] for c in pam)


def _locate_guide(target: str, guide: str, pam_regex: str) -> tuple[Literal["+", "-"], int]:
    """Find the guide in the target. Returns (strand, pam_start_on_strand) for the
    strand on which the guide+PAM pattern was found. Raises ToolError if not found OR
    if found at multiple positions ambiguously.
    """
    matches: list[tuple[Literal["+", "-"], int]] = []
    for strand, seq in (("+", target), ("-", str(Seq(target).reverse_complement()))):
        pattern = re.compile(re.escape(guide) + pam_regex)
        for m in pattern.finditer(seq):
            # m.start() points to start of guide. PAM begins at m.start() + len(guide).
            pam_start = m.start() + len(guide)
            matches.append((strand, pam_start))

    if not matches:
        raise ToolError(
            f"Guide {guide!r} not found adjacent to a PAM on either strand of the target. "
            "Verify the guide is the protospacer (NOT including the PAM), that it matches "
            "the target sequence exactly, and that the chosen PAM motif is correct for "
            "your nuclease."
        )
    if len(matches) > 1:
        positions = ", ".join(f"{s}@{p}" for s, p in matches)
        raise ToolError(
            f"Guide {guide!r} matches at multiple PAM-adjacent sites ({positions}). The "
            "edit outcome would be ambiguous. Lengthen the guide or extract a more "
            "specific protospacer."
        )
    return matches[0]


# --- Outcome generation -------------------------------------------------------------


def _generate_mmej_outcomes(
    *,
    target_fwd: str,
    cut_fwd: int,
    mmej_total_fraction: float,
    min_length: int,
) -> list[EditOutcome]:
    """Run the Bae 2014 microhomology scan and emit mmej_deletion outcomes.

    Probabilities sum to mmej_total_fraction (or 0 if no MH found). Each
    outcome carries the microhomology annotation so the agent/UI can show
    which MH pair templated the deletion.
    """
    mhs = find_microhomologies(target=target_fwd, cut_position=cut_fwd, min_length=min_length)
    if not mhs:
        return []
    shares = normalize_to_probabilities(microhomologies=mhs, mmej_fraction_of_total=mmej_total_fraction)
    outcomes: list[EditOutcome] = []
    for mh, prob in shares.items():
        edited = apply_mmej_deletion(target_fwd, mh)
        indel_size = -mh.deletion_size
        outcomes.append(
            EditOutcome(
                outcome_type="mmej_deletion",
                edited_sequence=edited,
                indel_size=indel_size,
                probability=round(prob, 4),
                frameshift=(indel_size % 3 != 0),
                notes=(
                    f"MMEJ deletion of {mh.deletion_size} nt templated by the "
                    f"{mh.length}-bp microhomology '{mh.sequence}' (Bae 2014 "
                    f"pattern score {mh.pattern_score:.2f}). The repair retains "
                    "a single copy of the MH; the right copy and intervening "
                    "bases are deleted."
                ),
                microhomology=MicrohomologyAnnotation(
                    sequence=mh.sequence,
                    length=mh.length,
                    left_position=mh.left_start,
                    right_position=mh.right_start,
                    pattern_score=round(mh.pattern_score, 4),
                ),
            )
        )
    return outcomes


def _generate_outcomes(
    target_fwd: str,
    cut_fwd: int,
    include_types: set[OutcomeType],
) -> list[EditOutcome]:
    """Produce the enumerated outcomes given the forward-strand cut position.

    All `edited_sequence` values are on the forward strand of the original input.
    """
    outcomes: list[EditOutcome] = []
    left = target_fwd[:cut_fwd]
    right = target_fwd[cut_fwd:]

    def maybe_add(otype: OutcomeType, edited: str, indel: int, notes: str = "") -> None:
        if otype not in include_types:
            return
        prob = _RULE_OF_THUMB_PROBS[otype]
        outcomes.append(
            EditOutcome(
                outcome_type=otype,
                edited_sequence=edited,
                indel_size=indel,
                probability=prob,
                frameshift=(indel % 3 != 0),
                notes=notes,
            )
        )

    maybe_add(
        "no_edit",
        target_fwd,
        0,
        notes="Perfect repair: NHEJ rejoins without altering the sequence.",
    )

    for base in ("A", "C", "G", "T"):
        otype: OutcomeType = f"insertion_+1_{base}"  # type: ignore[assignment]
        maybe_add(
            otype,
            left + base + right,
            1,
            notes=(
                f"Single +1 insertion of {base} at the cut site. "
                "Cas9 frequently inserts the templated base from the upstream nucleotide."
            ),
        )

    # Deletions: symmetric around the cut for -2, asymmetric for odd sizes.
    # Convention: split the deletion as evenly as possible across the cut, biased toward
    # the left flank (consistent with literature handling).
    for size, otype in ((1, "deletion_-1"), (2, "deletion_-2"), (3, "deletion_-3")):
        left_delete = (size + 1) // 2  # 1, 1, 2
        right_delete = size - left_delete  # 0, 1, 1
        edited = target_fwd[: cut_fwd - left_delete] + target_fwd[cut_fwd + right_delete :]
        maybe_add(
            otype,  # type: ignore[arg-type]
            edited,
            -size,
            notes=(
                f"-{size} nt deletion split across the cut (left:{left_delete}, "
                f"right:{right_delete}). Bias toward left flank follows literature convention."
            ),
        )

    # "deletion_larger" — aggregated bucket. Emit a representative example: -5 nt.
    if "deletion_larger" in include_types:
        rep_size = 5
        left_delete = (rep_size + 1) // 2
        right_delete = rep_size - left_delete
        # Only add if the target is large enough to support it cleanly.
        if cut_fwd - left_delete >= 0 and cut_fwd + right_delete <= len(target_fwd):
            edited = target_fwd[: cut_fwd - left_delete] + target_fwd[cut_fwd + right_delete :]
            outcomes.append(
                EditOutcome(
                    outcome_type="deletion_larger",
                    edited_sequence=edited,
                    indel_size=-rep_size,
                    probability=_RULE_OF_THUMB_PROBS["deletion_larger"],
                    frameshift=(rep_size % 3 != 0),
                    notes=(
                        "Aggregated bucket for deletions >3 nt (-4, -5, -10, etc.). "
                        "Representative shown is -5; actual size varies per repair event."
                    ),
                )
            )

    return outcomes


# --- Tool ----------------------------------------------------------------------------


@register_tool(
    name="edit_outcome",
    description=(
        "Predict the likely outcomes of a Cas9 edit at a given guide site. "
        "Locates the guide on either strand, computes the cut position, and "
        "enumerates two repair pathways: (1) NHEJ — perfect repair, +1 "
        "insertions, -1/-2/-3/larger deletions with published-average "
        "probabilities; (2) MMEJ — microhomology-templated deletions scored "
        "by the Bae 2014 algorithm using actual sequence context around the "
        "cut. The MMEJ branch makes probabilities GUIDE-AWARE — a cut in an "
        "MH-rich region produces sharper, more confident deletion predictions "
        "than a cut in low-complexity sequence. Each outcome carries an "
        "edited_sequence, indel_size, frameshift flag, probability, and (for "
        "MMEJ) the microhomology that templated it. Use whenever the user "
        "asks 'what does the edit look like?' or 'will this disrupt the gene?'."
    ),
    input_model=EditOutcomeInput,
    output_model=EditOutcomeOutput,
    version="2.0.0",
    citations=[
        "Bae S et al. (2014) Microhomology-based choice of Cas9 nuclease target sites. Nat Methods 11:705-706 (microhomology pattern score)",
        "Shen MW et al. (2018) Predictable and precise template-free CRISPR editing of pathogenic variants. Nature 563:646-651 (inDelphi MH component)",
        "Allen F et al. (2018) Predicting the mutations generated by repair of Cas9-induced double-strand breaks. Nat Biotechnol 37:64-72 (FORECasT)",
        "Chen W et al. (2019) Massively parallel profiling and predictive modeling of the outcomes of CRISPR/Cas9-mediated double-strand break repair. Nucleic Acids Res 47:7989-8003 (Lindel)",
        "van Overbeek M et al. (2016) DNA repair profiling reveals nonrandom outcomes at Cas9-mediated breaks. Mol Cell 63:633-646 (NHEJ + MMEJ frequency averages)",
    ],
    cost_hint="cheap",
    destructive=False,
    tags=["sequence", "crispr", "editing", "simulation"],
)
async def edit_outcome(inp: EditOutcomeInput) -> EditOutcomeOutput:
    pam_re = _pam_to_regex(inp.pam)
    strand, pam_start_on_strand = _locate_guide(inp.target, inp.guide, pam_re)

    # Determine cut position on the strand the guide is on.
    cut_on_strand = pam_start_on_strand - inp.cut_offset_from_pam
    if strand == "+":
        cut_fwd = cut_on_strand
    else:
        # Map back to forward strand: position L from end of - strand is position
        # len(target) - L on forward strand.
        cut_fwd = len(inp.target) - cut_on_strand

    if cut_fwd < 0 or cut_fwd > len(inp.target):
        raise ToolError(
            f"Computed cut position ({cut_fwd}) falls outside the target (length "
            f"{len(inp.target)}). The guide+PAM may be too close to a sequence boundary."
        )

    include_types: set[OutcomeType] = (
        set(inp.include_outcome_types) if inp.include_outcome_types else set(_RULE_OF_THUMB_PROBS) | {"mmej_deletion"}
    )

    nhej_outcomes = _generate_outcomes(inp.target, cut_fwd, include_types)
    mmej_outcomes: list[EditOutcome] = []
    if inp.enable_mmej and "mmej_deletion" in include_types and inp.mmej_total_fraction > 0:
        mmej_outcomes = _generate_mmej_outcomes(
            target_fwd=inp.target,
            cut_fwd=cut_fwd,
            mmej_total_fraction=inp.mmej_total_fraction,
            min_length=inp.min_microhomology_length,
        )

    # Re-scale NHEJ probabilities by (1 - actual_mmej_total) so the full
    # distribution sums to ~1.0. If we found NO MH (mmej_outcomes is empty),
    # NHEJ stays at its rule-of-thumb shape.
    actual_mmej_total = sum(o.probability for o in mmej_outcomes)
    if actual_mmej_total > 0 and nhej_outcomes:
        scale = 1.0 - actual_mmej_total
        nhej_outcomes = [o.model_copy(update={"probability": round(o.probability * scale, 4)}) for o in nhej_outcomes]

    outcomes = nhej_outcomes + mmej_outcomes

    if not outcomes:
        raise ToolError("No outcomes generated — `include_outcome_types` may have been too restrictive.")

    # Sort by probability descending so the user sees the most-likely outcomes first.
    outcomes.sort(key=lambda o: o.probability, reverse=True)

    caveats = [
        "NHEJ probabilities are published averages, NOT predictions for this "
        "specific guide. Per-guide ML models (inDelphi, FORECasT, Lindel) are "
        "a future slice and would replace the rule-of-thumb shares with "
        "sequence-context-aware values.",
        "Frameshift flagging only checks indel size modulo 3. Whether the edit "
        "actually disrupts a reading frame depends on whether the cut falls inside "
        "a CDS — the tool does not infer that.",
    ]
    if mmej_outcomes:
        caveats.append(
            f"MMEJ pathway: {len(mmej_outcomes)} microhomology-templated deletion(s) "
            f"detected; total MMEJ fraction set to {inp.mmej_total_fraction:.0%}. "
            "Probabilities within MMEJ are normalized from Bae 2014 pattern scores; "
            "NHEJ outcomes were rescaled by (1 - MMEJ total). Real cell-type-specific "
            "ratios vary."
        )
    else:
        caveats.append(
            "No microhomologies of length ≥"
            f"{inp.min_microhomology_length} found flanking the cut — MMEJ "
            "outcomes are not enumerated. NHEJ probabilities are NOT rescaled."
        )

    return EditOutcomeOutput(
        guide=inp.guide,
        guide_strand=strand,
        cut_position_fwd=cut_fwd,
        target_length=len(inp.target),
        outcomes=outcomes,
        summary_caveats=caveats,
    )
