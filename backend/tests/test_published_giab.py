"""Tests for publishing the GIAB concordance artifact (precision/recall/F1).

The generator is exercised with a MOCK GiabBenchmarkResult (no DeepVariant), writing to a
temp published dir, then loaded back. Also verifies the GIAB artifacts are kept separate from
the correlation-shaped PublishedBenchmark loader.
"""

from __future__ import annotations

from bioforge.benchmarks import published as pub
from bioforge.benchmarks.giab import GiabBenchmarkResult
from bioforge.benchmarks.variant_concordance import ConcordanceMetrics, VariantConcordanceResult


def _mock_result() -> GiabBenchmarkResult:
    return GiabBenchmarkResult(
        reference_build="GRCh38.test",
        regions="chr20:1-100",
        caller="DeepVariant test@sha256:deadbeef model_type=WGS",
        concordance=VariantConcordanceResult(
            by_class=[
                ConcordanceMetrics(variant_class="SNV", tp=45, fp=1, fn=0, precision=0.9783, recall=1.0, f1=0.989),
                ConcordanceMetrics(variant_class="INDEL", tp=4, fp=0, fn=0, precision=1.0, recall=1.0, f1=1.0),
                ConcordanceMetrics(variant_class="ALL", tp=49, fp=1, fn=0, precision=0.98, recall=1.0, f1=0.9899),
            ],
            n_truth_total=222,
            n_called_total=290,
            n_truth_in_regions=49,
            n_called_in_regions=50,
            caveat="genotype-agnostic, not haplotype-aware",
        ),
    )


def test_generate_and_load_giab_artifact(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(pub, "PUBLISHED_DIR", tmp_path)
    out = pub.generate_giab_artifact(
        result=_mock_result(),
        sample="NA12878 (HG001)",
        truth_set="NIST test region",
        interpretation="small validation region",
        name="GIAB test",
        slug="unit_test",
    )
    assert out.name == "giab_unit_test.json"
    assert out.exists()

    loaded = pub.load_published_giab()
    assert len(loaded) == 1
    art = loaded[0]
    assert art.sample == "NA12878 (HG001)"
    assert art.reference_build == "GRCh38.test"
    by = {m.variant_class: m for m in art.by_class}
    assert by["ALL"].precision == 0.98
    assert by["ALL"].recall == 1.0
    assert art.n_truth_in_regions == 49


def test_giab_artifacts_are_separate_from_correlation_loader(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(pub, "PUBLISHED_DIR", tmp_path)
    pub.generate_giab_artifact(
        result=_mock_result(),
        sample="s",
        truth_set="t",
        interpretation="i",
        name="n",
        slug="sep_test",
    )
    # The correlation-shaped loader must NOT pick up a giab_ artifact (different schema).
    assert pub.load_published_benchmarks() == []
    assert len(pub.load_published_giab()) == 1


def test_committed_na12878_giab_artifact_is_served_in_the_report() -> None:
    """The real committed artifact (DeepVariant vs NIST truth on NA12878 chr20:10-10.1Mb) shows
    up in the live Accuracy Report. This is the real, dated number -- not a fixture."""
    from bioforge.benchmarks.accuracy_report import build_accuracy_report

    report = build_accuracy_report()
    giab = [g for g in report.published_giab if "NA12878" in g.sample]
    assert giab, "the committed NA12878 GIAB artifact should be served in the report"
    by = {m.variant_class: m for m in giab[0].by_class}
    assert by["ALL"].tp == 49
    assert by["ALL"].recall == 1.0
    assert giab[0].n_truth_in_regions == 49
