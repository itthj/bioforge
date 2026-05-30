"""Typed schema for Azimuth / Doench Rule Set 2 on-target predictions.

The legacy subprocess returns plain JSON (`{"scores": [...]}`); we convert it into these
Pydantic models so tool outputs stay JSON-serializable, frontend-typeable, and trace-friendly
-- the same discipline the DeepCRISPR / inDelphi wrappers use.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AzimuthOnTargetScore(BaseModel):
    """One guide's Azimuth / Rule Set 2 on-target efficiency score."""

    thirtymer: str = Field(
        description=(
            "The 30 nt window scored: 4 nt 5' context + 20 nt protospacer + 3 nt PAM + 3 nt 3' "
            "context, 5'->3', DNA bases."
        ),
    )
    score: float = Field(
        description=(
            "Azimuth (Doench 2016 Rule Set 2) predicted on-target efficiency, typically on "
            "[0, 1]. Higher = predicted more efficient SpCas9 cleavage. This is the upstream "
            "model's own output -- do not rescale."
        ),
    )


class AzimuthOnTargetResult(BaseModel):
    """Batch result: one score per input 30-mer, in input order, plus provenance."""

    model: str = Field(description="Azimuth model id, e.g. 'V3_model_nopos'.")
    model_version: str = Field(
        description="Provenance tag combining the model id and the pinned upstream commit.",
    )
    scores: list[AzimuthOnTargetScore] = Field(
        description="One score per input 30-mer, returned in the same order as the input.",
    )
