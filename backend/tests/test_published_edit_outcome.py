"""Tests for publishing the edit-outcome distribution-agreement artifact (TVD/JSD).

The generator is exercised with a MOCK EditOutcomePublishedResult (no FORECasT/Docker), written
to a temp published dir then loaded back, and verified to stay separate from the other artifact
loaders (correlation-shaped + GIAB).
"""

from __future__ import annotations

from bioforge.benchmarks import published as pub
from bioforge.benchmarks.edit_outcome_published_run import EditOutcomePublishedResult, PerGuideAgreement


def _mock_result() -> EditOutcomePublishedResult:
    return EditOutcomePublishedResult(
        observed_sample="K562_LV7A_DPI7",
        sample_label="K562 (test)",
        model="forecast",
        model_version="allen-2018",
        target_library="self_target_oligos",
        min_reads=100,
        direction="FORWARD",
        n_eligible=10,
        n_joined=9,
        n_guides=3,
        n_skipped=0,
        join_coverage=0.9,
        tvd_median=0.45,
        tvd_q1=0.38,
        tvd_q3=0.55,
        jsd_median=0.30,
        jsd_q1=0.25,
        jsd_q3=0.35,
        leakage_status="unknown",
        leakage_caveat="IN-DISTRIBUTION agreement, not a held-out accuracy claim",
        observed_sha256="obs_sha",
        target_sha256="tgt_sha",
        citations=["observed cite", "library cite"],
        interpretation="median TVD 0.450 ...",
        per_guide=[
            PerGuideAgreement(oligo_id="Oligo10000", tvd=0.05, jsd=0.05, observed_reads=958, n_labels=200),
            PerGuideAgreement(oligo_id="Oligo10004", tvd=0.45, jsd=0.30, observed_reads=1569, n_labels=300),
            PerGuideAgreement(oligo_id="Oligo10005", tvd=0.95, jsd=0.60, observed_reads=737, n_labels=336),
        ],
    )


def test_generate_and_load_edit_outcome_artifact(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(pub, "PUBLISHED_DIR", tmp_path)
    out = pub.generate_edit_outcome_artifact(result=_mock_result())
    assert out.name == "edit_outcome_k562_lv7a_dpi7.json"
    assert out.exists()

    loaded = pub.load_published_edit_outcome()
    assert len(loaded) == 1
    art = loaded[0]
    assert art.tvd_median == 0.45
    assert art.direction == "FORWARD"
    assert art.n_guides == 3
    assert art.leakage_status == "unknown"
    assert "in-distribution" in art.leakage_caveat.lower()
    # Histogram: 10 [0,1] bins, counts sum to n_guides; tvd 0.10->bin0, 0.45->bin4, 0.95->bin9.
    assert sum(b.count for b in art.tvd_histogram) == 3
    assert art.tvd_histogram[0].count == 1
    assert art.tvd_histogram[4].count == 1
    assert art.tvd_histogram[9].count == 1


def test_edit_outcome_artifact_separate_from_other_loaders(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(pub, "PUBLISHED_DIR", tmp_path)
    pub.generate_edit_outcome_artifact(result=_mock_result())
    # The correlation-shaped + GIAB loaders must NOT pick up an edit_outcome_ artifact.
    assert pub.load_published_benchmarks() == []
    assert pub.load_published_giab() == []
    assert len(pub.load_published_edit_outcome()) == 1


def test_committed_edit_outcome_artifact_is_served_in_the_report() -> None:
    """The real committed artifact (FORECasT vs measured K562 profiles) shows up in the live
    Accuracy Report. This is the real, dated number -- not a fixture."""
    from bioforge.benchmarks.accuracy_report import build_accuracy_report

    report = build_accuracy_report()
    served = [e for e in report.published_edit_outcome if "FORECasT" in e.name]
    assert served, "the committed edit-outcome artifact should be served in the report"
    eo = served[0]
    assert eo.n_guides == 150
    assert eo.direction == "FORWARD"
    assert eo.min_reads == 100
    assert eo.leakage_status == "unknown"  # never a held-out claim from in-distribution data
    assert 0.0 < eo.tvd_median < 1.0  # a real measured distance, deterministic from the pinned inputs
    assert 0.0 < eo.jsd_median < 1.0
    assert sum(b.count for b in eo.tvd_histogram) == eo.n_guides
    assert "in-distribution" in eo.leakage_caveat.lower()
