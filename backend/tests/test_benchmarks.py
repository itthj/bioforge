"""§13 — Accuracy Report aggregation + API tests.

Verifies the platform publishes REAL self-measurement (the release-gated validator metrics +
registry-sourced model accuracy) and an HONEST ledger of unwired gold-sets — never a faked
benchmark number.
"""

from __future__ import annotations

import bioforge.tools  # noqa: F401 — register tools so the model ledger is populated
from bioforge.benchmarks.accuracy_report import AccuracyReport, build_accuracy_report

_SCORING_TOOLS = {"score_guide_on_target", "find_offtargets", "design_guides", "edit_outcome"}


def test_report_publishes_real_release_gated_validator_metrics() -> None:
    report = build_accuracy_report()
    assert isinstance(report, AccuracyReport)
    # The committed corpus is release-gated to perfect deterministic precision + recall.
    assert report.validator.metrics.n_cases > 0
    assert report.validator.threshold == 1.0
    assert report.validator.numeric_passes
    assert report.validator.entity_passes
    assert report.validator.passes


def test_report_model_ledger_comes_from_the_registry() -> None:
    report = build_accuracy_report()
    tools = {m.tool for m in report.models}
    # At least one of the scoring/heuristic tools carries §4.2 model metadata.
    assert tools & _SCORING_TOOLS, f"expected a scoring tool in the model ledger, got {sorted(tools)}"
    # Every listed entry actually carries provenance (never an empty row).
    for m in report.models:
        assert m.model_versions or m.published_accuracy


def test_report_benchmark_ledger_is_honest() -> None:
    report = build_accuracy_report()
    statuses = [b.status for b in report.benchmarks]
    assert "live" in statuses  # validator metrics are really measured
    # Every row carries one of the three honest states (never an unstated claim).
    assert set(statuses) <= {"live", "guard_only", "not_yet_wired"}
    # GIAB is mandated by §13 and must be present. Its caller is now wired (DeepVariant), so it
    # is guard_only -- runs offline over staged inputs, never faked on a page load.
    giab = next((b for b in report.benchmarks if "GIAB" in b.name), None)
    assert giab is not None and giab.status == "guard_only"


async def test_accuracy_endpoint_serves_the_report(streaming_client) -> None:
    resp = await streaming_client.get("/benchmarks/accuracy")
    assert resp.status_code == 200
    body = resp.json()
    assert body["validator"]["passes"] is True
    assert isinstance(body["models"], list) and body["models"]
    assert any(b["status"] == "live" for b in body["benchmarks"])
    # Every benchmark row is in the honest taxonomy (live / guard_only / not_yet_wired).
    assert all(b["status"] in {"live", "guard_only", "not_yet_wired"} for b in body["benchmarks"])
