"""§13 published-benchmark artifacts -- the real, dated measurements served in the report.

The artifact is generated offline (a Docker + network run); these tests assert the committed
artifact loads, is honest (held-out with sourced evidence), and flows into the Accuracy Report.
"""

from __future__ import annotations

from bioforge.benchmarks.accuracy_report import build_accuracy_report
from bioforge.benchmarks.published import PublishedBenchmark, load_published_benchmarks


def test_on_target_artifact_loads_and_is_honest() -> None:
    published = load_published_benchmarks()
    assert published, "expected the committed on-target artifact to be present"
    art = next((p for p in published if "DeepCRISPR" in p.name and "Chari-2015" in p.name), None)
    assert art is not None
    assert isinstance(art, PublishedBenchmark)
    # The real measured numbers (DeepCRISPR x Chari-2015, 1234 guides, cross-dataset).
    assert art.n == 1234
    assert 0.0 < art.spearman_rho < 0.3  # the genuine modest cross-dataset rho (live value ~0.130)
    assert art.dataset == "chari2015Train"
    # Honest leakage label travels with the artifact, with its primary-source evidence.
    assert art.leakage_status == "held_out"
    assert "Chuai 2018" in art.leakage_evidence or "PMC6020378" in art.leakage_evidence
    # The reliability curve behind the number is present and well-formed.
    assert art.reliability.n == 1234
    assert art.reliability.bins
    assert art.reliability.kind == "regression_ranking"


def test_off_target_artifact_loads_and_is_honest() -> None:
    published = load_published_benchmarks()
    art = next((p for p in published if "off-target" in p.name and "CFD" in p.name), None)
    assert art is not None
    assert art.dataset == "annotOfftargets"
    assert art.n > 500  # the validated-site corpus (~717 scored)
    assert 0.0 < art.spearman_rho < 0.6  # the genuine CFD-vs-readFraction discrimination (~0.31)
    # Honesty: leakage stays UNKNOWN (Doench-2016 training overlap unverified), with the caveat.
    assert art.leakage_status == "unknown"
    assert art.leakage_evidence == ""
    assert art.leakage_caveat  # the residual concern is recorded
    assert art.reliability.bins


def test_published_artifacts_flow_into_the_report() -> None:
    report = build_accuracy_report()
    assert report.published, "the Accuracy Report must surface the published artifacts"
    names = {p.name for p in report.published}
    assert any("DeepCRISPR" in n for n in names)  # on-target
    assert any("off-target" in n for n in names)  # off-target -- both arms publish real numbers
