"""Typed schema for FORECasT (Allen 2018) edit-outcome predictions.

FORECasT predicts a profile of indel outcomes for a gRNA. We carry its outcome-label ->
frequency map **verbatim** (no remapping into our own indel taxonomy) — faithful provenance
over a lossy re-encoding, consistent with the project's never-remap-upstream stance.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ForecastDistribution(BaseModel):
    """One FORECasT prediction for a single gRNA / target."""

    sequence_length: int = Field(description="Length of the target sequence scored.")
    predictions: dict[str, float] = Field(
        description=(
            "FORECasT's raw indel-outcome-label -> frequency map, verbatim (normalized to sum "
            "to ~1). Labels are FORECasT's own; they are NOT remapped into another taxonomy here."
        ),
    )
