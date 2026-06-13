"""Wet-lab feedback loop API (Limitation #4).

Closes the loop: the platform records a PREDICTION, the user runs the experiment and records the
measured OUTCOME, and the platform recomputes agreement / calibration over the matched pairs --
reusing benchmarks.reliability (ranking) and benchmarks.calibration (probability). The displayed
confidence then reflects the user's OWN results, not only published numbers.

Endpoints
---------
POST   /predictions                 Record one prediction (or a batch).
GET    /predictions?project_id=x     List predictions (newest first), with their outcomes.
POST   /predictions/{id}/outcome     Record the measured outcome for one prediction.
POST   /predictions/outcomes         Bulk-record outcomes by subject_key (the realistic path).
GET    /predictions/agreement?project_id=x&assay=y
                                     Recompute agreement over matched (predicted, observed) pairs.

Honesty rails (rule 18, §0):
  * Agreement is computed ONLY over predictions that have a recorded outcome. n_total / n_matched /
    n_pending are always reported so a sparse loop can never look complete.
  * The measured value is never fabricated or defaulted -- a prediction with no outcome is simply
    excluded. The user supplies every observed number.
  * kind="probability" requires binary outcomes (calibration is undefined otherwise) -> 422 with the
    underlying reason, never a silently coerced curve.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bioforge.api.auth import get_current_user, require_project_access
from bioforge.benchmarks.calibration import CalibrationCurve, calibration_curve
from bioforge.benchmarks.reliability import ReliabilityCurve, reliability_curve
from bioforge.db.engine import get_session
from bioforge.db.models import Prediction, User

router = APIRouter()

_KINDS = ("probability", "regression")


class PredictionIn(BaseModel):
    subject_key: str = Field(
        ..., description="Join key between a prediction and its outcome (guide seq, variant, sample)."
    )
    assay: str = Field(..., description="Human label for the quantity, e.g. 'on-target efficiency'.")
    predicted_value: float
    kind: str = Field(default="regression", description="'probability' (outcome 0/1 -> calibration) or 'regression'.")
    source: str | None = Field(default=None, description="Tool/model that produced the prediction.")


class RecordPredictionsRequest(BaseModel):
    project_id: str
    predictions: list[PredictionIn] = Field(..., min_length=1)


class PredictionOut(BaseModel):
    id: str
    project_id: str
    subject_key: str
    assay: str
    kind: str
    predicted_value: float
    source: str | None
    observed_value: float | None
    observed_at: str | None
    outcome_note: str | None
    created_at: str

    @classmethod
    def of(cls, p: Prediction) -> PredictionOut:
        return cls(
            id=p.id,
            project_id=p.project_id,
            subject_key=p.subject_key,
            assay=p.assay,
            kind=p.kind,
            predicted_value=p.predicted_value,
            source=p.source,
            observed_value=p.observed_value,
            observed_at=p.observed_at.isoformat() if p.observed_at else None,
            outcome_note=p.outcome_note,
            created_at=p.created_at.isoformat(),
        )


class OutcomeIn(BaseModel):
    observed_value: float
    note: str | None = None


class BulkOutcomeItem(BaseModel):
    subject_key: str
    observed_value: float
    note: str | None = None


class BulkOutcomesRequest(BaseModel):
    project_id: str
    assay: str | None = Field(default=None, description="If set, only predictions for this assay are matched.")
    outcomes: list[BulkOutcomeItem] = Field(..., min_length=1)


class AgreementResponse(BaseModel):
    project_id: str
    assay: str
    kind: str
    n_total: int = Field(description="Predictions recorded for this assay.")
    n_matched: int = Field(description="Predictions that now carry a measured outcome (feed the curve).")
    n_pending: int = Field(description="Predictions still awaiting a wet-lab outcome.")
    reliability: ReliabilityCurve | None = None
    calibration: CalibrationCurve | None = None


@router.post("/predictions", response_model=list[PredictionOut], status_code=201)
async def record_predictions(
    body: RecordPredictionsRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[PredictionOut]:
    await require_project_access(session, body.project_id, current_user)
    created: list[Prediction] = []
    for item in body.predictions:
        if item.kind not in _KINDS:
            raise HTTPException(status_code=422, detail=f"kind must be one of {_KINDS}; got {item.kind!r}.")
        p = Prediction(
            project_id=body.project_id,
            subject_key=item.subject_key,
            assay=item.assay,
            kind=item.kind,
            predicted_value=item.predicted_value,
            source=item.source,
        )
        session.add(p)
        created.append(p)
    await session.flush()
    await session.commit()
    for p in created:
        await session.refresh(p)
    return [PredictionOut.of(p) for p in created]


@router.get("/predictions", response_model=list[PredictionOut])
async def list_predictions(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[PredictionOut]:
    await require_project_access(session, project_id, current_user)
    result = await session.execute(
        select(Prediction).where(Prediction.project_id == project_id).order_by(Prediction.created_at.desc()).limit(500)
    )
    return [PredictionOut.of(p) for p in result.scalars().all()]


@router.post("/predictions/{prediction_id}/outcome", response_model=PredictionOut)
async def record_outcome(
    prediction_id: str,
    body: OutcomeIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> PredictionOut:
    p = await session.get(Prediction, prediction_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Prediction not found")
    await require_project_access(session, p.project_id, current_user)
    _validate_outcome_for_kind(p.kind, body.observed_value)
    p.observed_value = body.observed_value
    p.observed_at = datetime.now(UTC)
    p.outcome_note = body.note
    await session.commit()
    await session.refresh(p)
    return PredictionOut.of(p)


@router.post("/predictions/outcomes", response_model=dict)
async def record_outcomes_bulk(
    body: BulkOutcomesRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Bulk-record outcomes by subject_key -- the realistic wet-lab path (a plate of results).

    Each item updates ALL still-open predictions whose subject_key (and assay, if given) match.
    Returns how many predictions were matched and how many subject_keys had no open prediction."""
    await require_project_access(session, body.project_id, current_user)

    matched = 0
    unmatched_keys: list[str] = []
    for item in body.outcomes:
        stmt = select(Prediction).where(
            Prediction.project_id == body.project_id,
            Prediction.subject_key == item.subject_key,
            Prediction.observed_value.is_(None),
        )
        if body.assay:
            stmt = stmt.where(Prediction.assay == body.assay)
        rows = (await session.execute(stmt)).scalars().all()
        if not rows:
            unmatched_keys.append(item.subject_key)
            continue
        for p in rows:
            _validate_outcome_for_kind(p.kind, item.observed_value)
            p.observed_value = item.observed_value
            p.observed_at = datetime.now(UTC)
            p.outcome_note = item.note
            matched += 1
    await session.commit()
    return {"matched": matched, "unmatched_subject_keys": unmatched_keys}


@router.get("/predictions/agreement", response_model=AgreementResponse)
async def get_agreement(
    project_id: str,
    assay: str,
    n_bins: int = 10,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AgreementResponse:
    """Recompute agreement over the matched (predicted, observed) pairs for one assay.

    probability kind -> a CalibrationCurve (ECE/MCE/Brier; y=x is the target). regression kind ->
    a ranking ReliabilityCurve (monotonicity). The curve is omitted (null) when fewer than 2
    outcomes are in -- a calibration claim needs evidence -- but the counts are always returned."""
    await require_project_access(session, project_id, current_user)

    rows = (
        (
            await session.execute(
                select(Prediction).where(Prediction.project_id == project_id, Prediction.assay == assay)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"No predictions recorded for assay {assay!r}.")

    kind = rows[0].kind
    matched = [(r.predicted_value, r.observed_value) for r in rows if r.observed_value is not None]
    n_total = len(rows)
    n_matched = len(matched)

    reliability: ReliabilityCurve | None = None
    calibration: CalibrationCurve | None = None
    if n_matched >= 2:
        try:
            if kind == "probability":
                calibration = calibration_curve(
                    matched,
                    n_bins=n_bins,
                    kind="probability",
                    predicted_label=f"predicted {assay}",
                    observed_label="measured outcome",
                )
            else:
                reliability = reliability_curve(
                    matched,
                    n_bins=n_bins,
                    predicted_label=f"predicted {assay}",
                    observed_label="measured outcome",
                )
        except ValueError as e:
            # e.g. a probability assay whose outcomes were not 0/1. Surface the honest reason.
            raise HTTPException(status_code=422, detail=str(e)) from e

    return AgreementResponse(
        project_id=project_id,
        assay=assay,
        kind=kind,
        n_total=n_total,
        n_matched=n_matched,
        n_pending=n_total - n_matched,
        reliability=reliability,
        calibration=calibration,
    )


def _validate_outcome_for_kind(kind: str, observed_value: float) -> None:
    """A probability assay's outcome must be binary (0/1); calibration is undefined otherwise."""
    if kind == "probability" and observed_value not in (0.0, 1.0):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Assay kind 'probability' requires a binary outcome (0 or 1); got {observed_value}. "
                "Record a regression-kind prediction for a continuous measurement."
            ),
        )
