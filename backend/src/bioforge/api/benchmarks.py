"""§13 / §5 — Accuracy Report API.

`GET /benchmarks/accuracy` serves the platform's own measured accuracy: the grounding
validator's release-gated precision/recall, each scoring model's published accuracy
provenance, and an honest ledger of the gold-standard benchmarks that are not yet wired.
This is the backend behind the frontend "Accuracy Report" page (§5 architecture diagram).
"""

from __future__ import annotations

from fastapi import APIRouter

from bioforge.benchmarks.accuracy_report import AccuracyReport, build_accuracy_report

router = APIRouter()


@router.get("/benchmarks/accuracy", response_model=AccuracyReport)
async def get_accuracy_report() -> AccuracyReport:
    """Return the live self-measurement report. Pure compute — no DB, no side effects."""
    return build_accuracy_report()
