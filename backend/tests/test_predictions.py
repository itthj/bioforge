"""Tests for the wet-lab feedback loop API (Limitation #4).

Exercises the full loop hermetically: record predictions -> record outcomes (single + bulk) ->
recompute agreement (reliability for regression, calibration for probability). Plus the honesty
rails: agreement excludes un-matched predictions; a probability assay rejects non-binary outcomes.
"""

from __future__ import annotations

import pytest
from bioforge.constants import DEFAULT_PROJECT_ID
from httpx import AsyncClient


async def _record(client: AsyncClient, predictions: list[dict]) -> list[dict]:
    resp = await client.post("/predictions", json={"project_id": DEFAULT_PROJECT_ID, "predictions": predictions})
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_record_and_list_predictions(streaming_client: AsyncClient) -> None:
    created = await _record(
        streaming_client,
        [
            {"subject_key": "GUIDE_A", "assay": "on-target", "predicted_value": 0.8, "kind": "regression"},
            {"subject_key": "GUIDE_B", "assay": "on-target", "predicted_value": 0.3, "kind": "regression"},
        ],
    )
    assert len(created) == 2
    assert all(p["observed_value"] is None for p in created)  # loop not yet closed

    resp = await streaming_client.get(f"/predictions?project_id={DEFAULT_PROJECT_ID}")
    assert resp.status_code == 200
    assert {p["subject_key"] for p in resp.json()} >= {"GUIDE_A", "GUIDE_B"}


@pytest.mark.asyncio
async def test_record_outcome_closes_loop(streaming_client: AsyncClient) -> None:
    created = await _record(
        streaming_client,
        [{"subject_key": "G1", "assay": "eff", "predicted_value": 0.7, "kind": "regression"}],
    )
    pid = created[0]["id"]
    resp = await streaming_client.post(f"/predictions/{pid}/outcome", json={"observed_value": 0.65, "note": "n=3"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["observed_value"] == 0.65
    assert body["observed_at"] is not None
    assert body["outcome_note"] == "n=3"


@pytest.mark.asyncio
async def test_bulk_outcomes_by_subject_key(streaming_client: AsyncClient) -> None:
    await _record(
        streaming_client,
        [
            {"subject_key": "S1", "assay": "eff", "predicted_value": 0.9, "kind": "regression"},
            {"subject_key": "S2", "assay": "eff", "predicted_value": 0.2, "kind": "regression"},
        ],
    )
    resp = await streaming_client.post(
        "/predictions/outcomes",
        json={
            "project_id": DEFAULT_PROJECT_ID,
            "assay": "eff",
            "outcomes": [
                {"subject_key": "S1", "observed_value": 0.88},
                {"subject_key": "S2", "observed_value": 0.25},
                {"subject_key": "UNKNOWN", "observed_value": 0.5},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched"] == 2
    assert body["unmatched_subject_keys"] == ["UNKNOWN"]


@pytest.mark.asyncio
async def test_agreement_regression_builds_reliability_curve(streaming_client: AsyncClient) -> None:
    preds = [
        {"subject_key": f"g{i}", "assay": "ranktest", "predicted_value": i / 10.0, "kind": "regression"}
        for i in range(10)
    ]
    await _record(streaming_client, preds)
    # Record outcomes that track the prediction (monotone) -> high monotonicity.
    await streaming_client.post(
        "/predictions/outcomes",
        json={
            "project_id": DEFAULT_PROJECT_ID,
            "assay": "ranktest",
            "outcomes": [{"subject_key": f"g{i}", "observed_value": i / 10.0} for i in range(10)],
        },
    )
    resp = await streaming_client.get(f"/predictions/agreement?project_id={DEFAULT_PROJECT_ID}&assay=ranktest&n_bins=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "regression"
    assert body["n_total"] == 10
    assert body["n_matched"] == 10
    assert body["n_pending"] == 0
    assert body["reliability"] is not None
    assert body["calibration"] is None
    assert body["reliability"]["monotonicity_rho"] == pytest.approx(1.0, abs=1e-6)


@pytest.mark.asyncio
async def test_agreement_probability_builds_calibration_curve(streaming_client: AsyncClient) -> None:
    # 20 predictions, perfectly calibrated: P=0 -> outcome 0, P=1 -> outcome 1.
    preds = [
        {"subject_key": f"lo{i}", "assay": "patho", "predicted_value": 0.0, "kind": "probability"} for i in range(10)
    ] + [{"subject_key": f"hi{i}", "assay": "patho", "predicted_value": 1.0, "kind": "probability"} for i in range(10)]
    await _record(streaming_client, preds)
    outcomes = [{"subject_key": f"lo{i}", "observed_value": 0.0} for i in range(10)] + [
        {"subject_key": f"hi{i}", "observed_value": 1.0} for i in range(10)
    ]
    await streaming_client.post(
        "/predictions/outcomes",
        json={"project_id": DEFAULT_PROJECT_ID, "assay": "patho", "outcomes": outcomes},
    )
    resp = await streaming_client.get(f"/predictions/agreement?project_id={DEFAULT_PROJECT_ID}&assay=patho")
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "probability"
    assert body["calibration"] is not None
    assert body["reliability"] is None
    assert body["calibration"]["ece"] == pytest.approx(0.0, abs=1e-9)
    assert body["calibration"]["brier"] == pytest.approx(0.0, abs=1e-9)


@pytest.mark.asyncio
async def test_agreement_excludes_pending(streaming_client: AsyncClient) -> None:
    await _record(
        streaming_client,
        [
            {"subject_key": "m1", "assay": "partial", "predicted_value": 0.5, "kind": "regression"},
            {"subject_key": "m2", "assay": "partial", "predicted_value": 0.6, "kind": "regression"},
            {"subject_key": "m3", "assay": "partial", "predicted_value": 0.7, "kind": "regression"},
        ],
    )
    # Only two get outcomes -> n_matched=2, n_pending=1.
    await streaming_client.post(
        "/predictions/outcomes",
        json={
            "project_id": DEFAULT_PROJECT_ID,
            "assay": "partial",
            "outcomes": [{"subject_key": "m1", "observed_value": 0.4}, {"subject_key": "m2", "observed_value": 0.55}],
        },
    )
    resp = await streaming_client.get(f"/predictions/agreement?project_id={DEFAULT_PROJECT_ID}&assay=partial")
    body = resp.json()
    assert body["n_total"] == 3
    assert body["n_matched"] == 2
    assert body["n_pending"] == 1


@pytest.mark.asyncio
async def test_probability_outcome_must_be_binary(streaming_client: AsyncClient) -> None:
    created = await _record(
        streaming_client,
        [{"subject_key": "p1", "assay": "binprob", "predicted_value": 0.7, "kind": "probability"}],
    )
    pid = created[0]["id"]
    # A non-binary outcome for a probability assay is rejected (calibration undefined).
    resp = await streaming_client.post(f"/predictions/{pid}/outcome", json={"observed_value": 0.5})
    assert resp.status_code == 422
    assert "binary" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_agreement_too_few_matched_returns_counts_no_curve(streaming_client: AsyncClient) -> None:
    created = await _record(
        streaming_client,
        [{"subject_key": "only1", "assay": "sparse", "predicted_value": 0.5, "kind": "regression"}],
    )
    await streaming_client.post(f"/predictions/{created[0]['id']}/outcome", json={"observed_value": 0.5})
    resp = await streaming_client.get(f"/predictions/agreement?project_id={DEFAULT_PROJECT_ID}&assay=sparse")
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_matched"] == 1
    assert body["reliability"] is None  # needs >= 2 points; honest about insufficient evidence
    assert body["calibration"] is None


@pytest.mark.asyncio
async def test_agreement_unknown_assay_404(streaming_client: AsyncClient) -> None:
    resp = await streaming_client.get(f"/predictions/agreement?project_id={DEFAULT_PROJECT_ID}&assay=nope")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_invalid_kind_rejected(streaming_client: AsyncClient) -> None:
    resp = await streaming_client.post(
        "/predictions",
        json={
            "project_id": DEFAULT_PROJECT_ID,
            "predictions": [{"subject_key": "x", "assay": "a", "predicted_value": 1.0, "kind": "bogus"}],
        },
    )
    assert resp.status_code == 422
