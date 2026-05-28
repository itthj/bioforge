"""Typed schema for inDelphi predictions.

Upstream returns a pandas DataFrame + an untyped dict. We convert into these
Pydantic models so tool outputs stay JSON-serializable, frontend-typeable,
and trace-friendly. All field semantics map 1:1 to the upstream columns;
see `inference._map_result` for the conversion.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

OutcomeCategory = Literal["deletion", "insertion"]


class InDelphiOutcome(BaseModel):
    """One predicted indel — either a deletion of `length` bases or an
    insertion of `inserted_bases` (length 1 for current inDelphi)."""

    category: OutcomeCategory = Field(description="Repair-outcome class predicted by inDelphi.")
    length: int = Field(
        description=(
            "Indel size in bp. Always positive in the schema; sign is implied by `category` "
            "(deletion → bases removed, insertion → bases inserted)."
        ),
        ge=1,
    )
    genotype_position: int | None = Field(
        default=None,
        description=(
            "0-based offset (relative to `cutsite`) of the indel boundary on the forward "
            "strand. `None` for 'elsewhere' deletion buckets that inDelphi aggregates "
            "without a single position."
        ),
    )
    inserted_bases: str | None = Field(
        default=None,
        description="The inserted base(s) for insertions; `None` for deletions.",
    )
    predicted_frequency: float = Field(
        description=(
            "Predicted frequency as a percentage (0–100). Sums across outcomes ≈ 100 "
            "(modulo MH-less long-tail truncation)."
        ),
        ge=0.0,
    )


class InDelphiStats(BaseModel):
    """Aggregate summary stats returned by upstream `inDelphi.predict()`.

    Field names are snake_case versions of upstream keys; values pass through
    unchanged. Defaults are 0.0 so a partial dict doesn't poison the schema.
    """

    phi: float = 0.0
    precision: float = 0.0
    frameshift_frequency: float = 0.0
    frame_plus_0_frequency: float = 0.0
    frame_plus_1_frequency: float = 0.0
    frame_plus_2_frequency: float = 0.0
    mh_del_frequency: float = 0.0
    mhless_del_frequency: float = 0.0
    one_bp_ins_frequency: float = 0.0
    highest_outcome_frequency: float = 0.0
    highest_del_frequency: float = 0.0
    highest_ins_frequency: float = 0.0
    expected_indel_length: float = 0.0


class InDelphiDistribution(BaseModel):
    """Full inDelphi result: per-outcome rows + summary stats + provenance."""

    cell_type: str = Field(description="Cell type the active model was trained on (mESC, U2OS, ...).")
    cutsite: int = Field(
        description="0-based cut position used for prediction (Cas9 blunt cut between cutsite-1 and cutsite)."
    )
    sequence_length: int = Field(description="Length of the target sequence passed to inDelphi.")
    outcomes: list[InDelphiOutcome] = Field(
        description="Predicted indel outcomes, sorted by `predicted_frequency` descending.",
    )
    stats: InDelphiStats = Field(description="Aggregate metrics summarizing the distribution.")
