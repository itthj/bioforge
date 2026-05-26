"""Tests for design_primers.

primer3's output is deterministic for a given (template, constraints) pair, so we can
pin specific assertions against constructed templates without flakiness. The tests
focus on:
  - Input validation (range checks, alphabet, target-region bounds)
  - The conversion from primer3's flat dict to the PrimerPair list shape
  - Specific structural properties (primers flank the target, product size in range)
  - The honest caveats the agent's responder needs to surface

Real primer3 is invoked — no mocking. It's a C extension that runs in microseconds,
adds no flakiness, and exercises the actual API surface the tool depends on.
"""

from __future__ import annotations

import pydantic
import pytest
from bioforge.tools.sequence.design_primers import (
    DesignPrimersInput,
    design_primers,
)

# A 200-nt template with balanced base composition and no extreme runs — primer3
# returns multiple valid pairs against this consistently across versions. Built by
# hand so the test isn't tied to any particular genome.
_TEMPLATE = (
    "GCAATTCCCAATGGCAAAGGTAAAATCCATCGTAACGTGGAATCCAAATAAGGCATATATATGCAACCGATACG"
    "TAAGCAGTACCGGTGAACGTGGCTTAATGCCCTTGACATAGCCGTATCAATGGTTCCAAGGCTCTAGGTTCGAT"
    "CGTACCGTACGATACGAATGGCATTTAGCATGAAGTCATAGCCTTAGCATTGCAACTGCATGCAA"
)


# --- Validation -----------------------------------------------------------------------


async def test_rejects_template_with_non_dna_chars() -> None:
    with pytest.raises(pydantic.ValidationError, match="non-DNA"):
        DesignPrimersInput(template=_TEMPLATE[:50] + "Z" + _TEMPLATE[50:])


async def test_rejects_too_short_template() -> None:
    with pytest.raises(pydantic.ValidationError):
        DesignPrimersInput(template="ATGCATGC")  # < min_length=40


async def test_rejects_inverted_product_size() -> None:
    with pytest.raises(pydantic.ValidationError, match="product_size_min must be"):
        DesignPrimersInput(template=_TEMPLATE, product_size_min=300, product_size_max=100)


async def test_rejects_tm_optimal_outside_range() -> None:
    with pytest.raises(pydantic.ValidationError, match="primer_tm_optimal must be within"):
        DesignPrimersInput(
            template=_TEMPLATE,
            primer_tm_min=58.0,
            primer_tm_max=62.0,
            primer_tm_optimal=70.0,
        )


async def test_rejects_target_outside_template() -> None:
    with pytest.raises(pydantic.ValidationError, match="target_end .* exceeds template length"):
        DesignPrimersInput(template=_TEMPLATE, target_start=0, target_end=len(_TEMPLATE) + 50)


async def test_rejects_one_sided_target() -> None:
    with pytest.raises(pydantic.ValidationError, match="must both be set, or both None"):
        DesignPrimersInput(template=_TEMPLATE, target_start=10)


# --- Successful design ----------------------------------------------------------------


async def test_returns_primer_pairs_for_open_design() -> None:
    """No target constraint — primer3 should find pairs anywhere along the template."""
    out = await design_primers(
        DesignPrimersInput(
            template=_TEMPLATE,
            product_size_min=80,
            product_size_max=150,
            max_primer_pairs=5,
        )
    )
    assert out.template_length == len(_TEMPLATE)
    assert out.num_returned >= 1
    assert out.num_returned <= 5
    assert len(out.primer_pairs) == out.num_returned


async def test_primer_pair_has_complete_fields() -> None:
    out = await design_primers(DesignPrimersInput(template=_TEMPLATE, max_primer_pairs=1))
    assert out.num_returned >= 1
    p = out.primer_pairs[0]
    # Each field that we promise the agent should be present + sane.
    assert p.rank == 0
    assert len(p.forward_sequence) >= 18
    assert len(p.reverse_sequence) >= 18
    assert 18 <= p.forward_length <= 25
    assert 18 <= p.reverse_length <= 25
    assert 58.0 <= p.forward_tm <= 62.0
    assert 58.0 <= p.reverse_tm <= 62.0
    assert 80 <= p.product_size <= 300  # default range
    assert p.pair_penalty >= 0.0


async def test_target_region_constrains_primer_placement() -> None:
    """When SEQUENCE_TARGET is set, primers must flank it (forward upstream, reverse
    downstream). primer3 enforces this; we verify the output respects it."""
    target_start = 80
    target_end = 130
    out = await design_primers(
        DesignPrimersInput(
            template=_TEMPLATE,
            target_start=target_start,
            target_end=target_end,
            product_size_min=80,
            product_size_max=200,
            max_primer_pairs=3,
        )
    )
    assert out.num_returned >= 1
    for pair in out.primer_pairs:
        # Forward primer's 3'-end must be at or before target_start.
        fwd_end = pair.forward_start + pair.forward_length
        assert fwd_end <= target_start, f"Forward primer ends at {fwd_end} but target starts at {target_start}"
        # Reverse primer's 3'-end (reverse_start, on the forward strand) must be at
        # or after target_end.
        assert pair.reverse_start >= target_end, (
            f"Reverse primer ends at {pair.reverse_start} but target ends at {target_end}"
        )


async def test_product_size_in_requested_range() -> None:
    out = await design_primers(
        DesignPrimersInput(
            template=_TEMPLATE,
            product_size_min=100,
            product_size_max=180,
            max_primer_pairs=5,
        )
    )
    assert out.num_returned >= 1
    for pair in out.primer_pairs:
        assert 100 <= pair.product_size <= 180


async def test_max_primer_pairs_caps_response() -> None:
    out = await design_primers(DesignPrimersInput(template=_TEMPLATE, max_primer_pairs=2))
    assert out.num_returned <= 2


# --- Failure mode: no primers possible ------------------------------------------------


async def test_no_pairs_returned_when_constraints_too_strict() -> None:
    """Demand a Tm range that no primer in this template can satisfy. primer3
    returns 0 pairs and we surface the explain strings via primer3_warnings."""
    out = await design_primers(
        DesignPrimersInput(
            template=_TEMPLATE,
            primer_tm_min=75.0,
            primer_tm_max=78.0,
            primer_tm_optimal=76.0,
            max_primer_pairs=5,
        )
    )
    assert out.num_returned == 0
    assert out.primer_pairs == []
    # primer3's explain output is surfaced so the agent can tell the user why.
    assert len(out.primer3_warnings) > 0


# --- Honesty / caveats ----------------------------------------------------------------


async def test_caveats_mention_specificity_gap_and_tm_estimation() -> None:
    out = await design_primers(DesignPrimersInput(template=_TEMPLATE, max_primer_pairs=1))
    text = " ".join(out.caveats).lower()
    assert "specificity" in text
    assert "blast" in text  # explicitly points at the composing tool
    assert "tm" in text


async def test_is_registered_as_cheap_pcr_tool() -> None:
    """Pure compute — no network, no expensive thermodynamics. Planner can call
    freely without triggering the approval gate."""
    from bioforge.tools.registry import get_tool

    spec = get_tool("design_primers")
    assert spec.cost_hint == "cheap"
    assert "primer" in spec.tags
    assert "pcr" in spec.tags
    assert spec.destructive is False


# --- Composition with the rest of the toolbox -----------------------------------------


async def test_pairs_are_substrings_of_template_or_revcomp() -> None:
    """Sanity-check: the forward primer must be a substring of the template at its
    reported coordinates; the reverse primer must be a substring of the template's
    reverse complement. If primer3 ever shipped coordinates that didn't line up
    with the sequence, this would catch it."""
    from Bio.Seq import Seq

    out = await design_primers(DesignPrimersInput(template=_TEMPLATE, max_primer_pairs=1))
    pair = out.primer_pairs[0]

    fwd_slice = _TEMPLATE[pair.forward_start : pair.forward_start + pair.forward_length]
    assert pair.forward_sequence == fwd_slice

    # Reverse primer's 5'→3' sequence equals the reverse-complement of the
    # template segment it covers. The reported reverse_start is the 3'-end on the
    # forward strand (inclusive), so the segment is template[start - length + 1 : start + 1].
    rc_seg_start = pair.reverse_start - pair.reverse_length + 1
    rc_seg_end = pair.reverse_start + 1
    fwd_strand_segment = _TEMPLATE[rc_seg_start:rc_seg_end]
    expected_reverse = str(Seq(fwd_strand_segment).reverse_complement())
    assert pair.reverse_sequence == expected_reverse
