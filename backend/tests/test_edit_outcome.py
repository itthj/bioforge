"""Tests for edit_outcome.

Strategy: construct target sequences with EXACTLY ONE guide+PAM site at a known
position so cut-position math and strand handling can be verified deterministically.
Each test owns its expected coordinates via the construction.
"""

from __future__ import annotations

import pydantic
import pytest
from Bio.Seq import Seq
from bioforge.tools.base import ToolError
from bioforge.tools.sequence.edit_outcome import (
    _RULE_OF_THUMB_PROBS,
    EditOutcomeInput,
    edit_outcome,
)

# 20-nt protospacer used as a stable guide across tests. Balanced GC, no runs.
_GUIDE = "ACGTACGTACGTACGTACGT"

# A 60-nt forward-strand target containing exactly ONE guide+NGG site at positions
# [20..40] (guide) and [40..43] (PAM). Flanking filler differs from the guide so we
# don't get unintended matches.
_TARGET_FWD = (
    "AAAAATTTTAAAAATTTTAA"  # 20-nt 5' filler (no guide-matching seq)
    + _GUIDE                # protospacer at [20..40]
    + "AGG"                 # NGG PAM at [40..43]
    + "CCCCCCCCCCCCCCCCC"   # 17-nt 3' filler (total 60 nt)
)


async def test_locates_guide_on_forward_strand() -> None:
    out = await edit_outcome(
        EditOutcomeInput(target=_TARGET_FWD, guide=_GUIDE)
    )
    assert out.guide_strand == "+"
    # Cas9 cuts 3 nt upstream of PAM. PAM at fwd[40..43]; cut at fwd position 37.
    assert out.cut_position_fwd == 37


async def test_locates_guide_on_reverse_strand() -> None:
    """Reverse-complement the construct so the guide+PAM live on the - strand."""
    rev_construct = str(Seq(_TARGET_FWD).reverse_complement())
    out = await edit_outcome(
        EditOutcomeInput(target=rev_construct, guide=_GUIDE)
    )
    assert out.guide_strand == "-"
    # On the original + strand the cut was at position 37 of a 60-nt sequence.
    # On the reverse-complemented strand (now the input), that point maps to 60 - 37 = 23.
    assert out.cut_position_fwd == 23


async def test_no_match_raises_clear_error() -> None:
    with pytest.raises(ToolError, match="not found"):
        await edit_outcome(
            EditOutcomeInput(
                target="A" * 50,  # all A's: no guide match possible
                guide=_GUIDE,
            )
        )


async def test_ambiguous_match_raises_clear_error() -> None:
    """Guide appears at TWO PAM-adjacent sites → ambiguous edit, must refuse."""
    one_hit = _GUIDE + "AGG"  # 23 nt
    spacer = "TTTTT" * 4       # 20 nt of T's (no guide-matching content)
    target = "A" * 10 + one_hit + spacer + one_hit + "A" * 10
    with pytest.raises(ToolError, match="multiple PAM-adjacent sites"):
        await edit_outcome(EditOutcomeInput(target=target, guide=_GUIDE))


# --- Outcome enumeration -------------------------------------------------------------


async def test_emits_all_nine_standard_outcomes() -> None:
    out = await edit_outcome(EditOutcomeInput(target=_TARGET_FWD, guide=_GUIDE))
    types = {o.outcome_type for o in out.outcomes}
    assert types == set(_RULE_OF_THUMB_PROBS)


async def test_no_edit_returns_unchanged_target() -> None:
    out = await edit_outcome(EditOutcomeInput(target=_TARGET_FWD, guide=_GUIDE))
    no_edit = next(o for o in out.outcomes if o.outcome_type == "no_edit")
    assert no_edit.edited_sequence == _TARGET_FWD
    assert no_edit.indel_size == 0
    assert no_edit.frameshift is False


async def test_insertion_adds_one_base_at_cut() -> None:
    out = await edit_outcome(EditOutcomeInput(target=_TARGET_FWD, guide=_GUIDE))
    for base in ("A", "C", "G", "T"):
        outcome = next(
            o for o in out.outcomes if o.outcome_type == f"insertion_+1_{base}"
        )
        assert outcome.indel_size == 1
        assert outcome.frameshift is True  # +1 % 3 != 0
        assert len(outcome.edited_sequence) == len(_TARGET_FWD) + 1
        # The inserted base lands at the cut position (fwd index 37)
        assert outcome.edited_sequence[37] == base


async def test_deletion_minus_3_is_in_frame() -> None:
    out = await edit_outcome(EditOutcomeInput(target=_TARGET_FWD, guide=_GUIDE))
    d3 = next(o for o in out.outcomes if o.outcome_type == "deletion_-3")
    assert d3.indel_size == -3
    assert d3.frameshift is False  # -3 % 3 == 0
    assert len(d3.edited_sequence) == len(_TARGET_FWD) - 3


async def test_deletion_minus_1_is_frameshift() -> None:
    out = await edit_outcome(EditOutcomeInput(target=_TARGET_FWD, guide=_GUIDE))
    d1 = next(o for o in out.outcomes if o.outcome_type == "deletion_-1")
    assert d1.indel_size == -1
    assert d1.frameshift is True


async def test_probabilities_sum_to_approximately_one() -> None:
    out = await edit_outcome(EditOutcomeInput(target=_TARGET_FWD, guide=_GUIDE))
    total = sum(o.probability for o in out.outcomes)
    assert total == pytest.approx(1.0, abs=0.01)


async def test_outcomes_sorted_by_probability_desc() -> None:
    out = await edit_outcome(EditOutcomeInput(target=_TARGET_FWD, guide=_GUIDE))
    probs = [o.probability for o in out.outcomes]
    assert probs == sorted(probs, reverse=True)
    # no_edit is always the most-likely outcome under the rule-of-thumb table
    assert out.outcomes[0].outcome_type == "no_edit"


async def test_include_outcome_types_filters_response() -> None:
    out = await edit_outcome(
        EditOutcomeInput(
            target=_TARGET_FWD,
            guide=_GUIDE,
            include_outcome_types=["deletion_-3", "no_edit"],
        )
    )
    types = {o.outcome_type for o in out.outcomes}
    assert types == {"deletion_-3", "no_edit"}


# --- Caveats / honesty ---------------------------------------------------------------


async def test_output_carries_required_caveats() -> None:
    """The agent's responder MUST see these caveats so it can surface them to the user."""
    out = await edit_outcome(EditOutcomeInput(target=_TARGET_FWD, guide=_GUIDE))
    text = " ".join(out.summary_caveats).lower()
    assert "average" in text or "averages" in text
    assert "not" in text and "prediction" in text
    assert "frameshift" in text
    assert "mmej" in text or "microhomology" in text


# --- Adversarial validation ----------------------------------------------------------


async def test_rejects_short_target() -> None:
    with pytest.raises(pydantic.ValidationError):
        EditOutcomeInput(target="ATGC" * 5, guide=_GUIDE)  # 20 nt < min 30


async def test_rejects_non_dna_target() -> None:
    with pytest.raises(pydantic.ValidationError, match="non-DNA"):
        EditOutcomeInput(target="A" * 25 + "Z" + "C" * 25, guide=_GUIDE)


async def test_rejects_unsupported_pam_chars() -> None:
    with pytest.raises(pydantic.ValidationError, match="unsupported characters"):
        EditOutcomeInput(target=_TARGET_FWD, guide=_GUIDE, pam="NGQ")


async def test_is_registered_with_crispr_and_editing_tags() -> None:
    from bioforge.tools.registry import get_tool

    spec = get_tool("edit_outcome")
    assert spec.cost_hint == "cheap"
    assert "crispr" in spec.tags
    assert "editing" in spec.tags
    assert spec.destructive is False


# --- Composition with design_guides (the canonical workflow) -------------------------


async def test_composes_with_design_guides_output() -> None:
    """A guide selected by design_guides should be usable by edit_outcome unchanged."""
    from bioforge.tools.sequence.design_guides import (
        DesignGuidesInput,
        design_guides,
    )

    design_out = await design_guides(DesignGuidesInput(sequence=_TARGET_FWD, strands=["+"]))
    assert design_out.num_returned >= 1
    top_guide = design_out.guides[0].protospacer

    edit_out = await edit_outcome(
        EditOutcomeInput(target=_TARGET_FWD, guide=top_guide)
    )
    # End-to-end: design_guides → edit_outcome found the same site
    assert edit_out.guide_strand == "+"
    assert any(o.outcome_type == "deletion_-3" for o in edit_out.outcomes)
