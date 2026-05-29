"""Typed schema for DeepCRISPR on-target predictions.

The legacy subprocess returns plain JSON (`{"scores": [...]}`); we convert it into
these Pydantic models so tool outputs stay JSON-serializable, frontend-typeable, and
trace-friendly — the same discipline the inDelphi wrapper uses.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DeepCRISPROnTargetScore(BaseModel):
    """One guide's DeepCRISPR on-target efficacy score."""

    guide: str = Field(
        description="The 23 bp window scored: 20 nt protospacer + 3 nt PAM, 5'->3', DNA bases.",
    )
    score: float = Field(
        description=(
            "DeepCRISPR sequence-only CNN regression on-target efficacy score. Higher = "
            "predicted more efficient SpCas9 cleavage. The exact normalization/range is "
            "the upstream model's own (confirm at numeric validation); do not assume a "
            "fixed [0, 1] interval here."
        ),
    )


class DeepCRISPROnTargetResult(BaseModel):
    """Batch result: one score per input guide, in input order, plus provenance."""

    model: str = Field(description="DeepCRISPR model id, e.g. 'ontar_cnn_reg_seq'.")
    model_version: str = Field(
        description="Provenance tag combining the model id and the pinned upstream commit.",
    )
    scores: list[DeepCRISPROnTargetScore] = Field(
        description="One score per input guide, returned in the same order as the input.",
    )
