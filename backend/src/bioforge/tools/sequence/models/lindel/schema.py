"""Typed schema for Lindel (Chen 2019) edit-outcome predictions.

Lindel's `gen_prediction` returns a probability distribution over indel outcome *labels*
plus a frameshift ratio. We carry the label->frequency map **verbatim** (no remapping into
our own indel taxonomy) — faithful provenance over a lossy re-encoding, consistent with the
project's never-remap-upstream stance.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LindelDistribution(BaseModel):
    """One Lindel prediction for a single 60 bp edit window."""

    sequence_length: int = Field(description="Length of the window scored (Lindel requires exactly 60 bp).")
    frameshift_ratio: float = Field(
        description="Lindel's predicted fraction of outcomes that shift the reading frame (0-1).",
    )
    predictions: dict[str, float] = Field(
        description=(
            "Lindel's raw outcome-label -> predicted frequency map, verbatim. Labels are "
            "Lindel's own (e.g. deletion length+position / 1-bp insertion identity); they are "
            "NOT remapped into another taxonomy here. Frequencies sum to ~1."
        ),
    )
