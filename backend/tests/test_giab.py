"""Tests for the GIAB end-to-end concordance benchmark (DeepVariant caller + scorer wiring).

The pure pieces (BED + VCF parsing, the scoring core) are tested on a small designed scenario;
the DeepVariant invocation is tested via build_command (argv) and run_giab_benchmark with a
MOCK caller -- no Docker, no downloads. The real run is a separate -m docker/-m online effort.
"""

from __future__ import annotations

import pytest
from bioforge.benchmarks.deepvariant_runner import (
    DeepVariantUnavailable,
    build_command,
)
from bioforge.benchmarks.giab import (
    GiabUnavailable,
    giab_quality_calibration,
    parse_confident_regions,
    qualified_calls_from_vcf_text,
    run_giab_benchmark,
    score_giab,
    variant_calls_from_vcf_text,
)
from bioforge.config import settings

_TRUTH_VCF = (
    "##fileformat=VCFv4.2\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
    "chr20\t1001\t.\tA\tG\t.\tPASS\t.\n"  # SNV, in region
    "chr20\t1500\t.\tC\tT\t.\tPASS\t.\n"  # SNV, in region
    "chr20\t1600\t.\tAC\tA\t.\tPASS\t.\n"  # INDEL (deletion), in region
    "chr20\t5000\t.\tA\tG\t.\tPASS\t.\n"  # SNV, OUTSIDE the confident region
)
_CALLED_VCF = (
    "##fileformat=VCFv4.2\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
    "chr20\t1001\t.\tA\tG\t.\tPASS\t.\n"  # TP (SNV)
    "chr20\t1500\t.\tC\tT\t.\tPASS\t.\n"  # TP (SNV)
    "chr20\t1700\t.\tG\tA\t.\tPASS\t.\n"  # FP (SNV, in region, no truth)
    # misses the INDEL at 1600 -> FN
)
_BED = "chr20\t1000\t2000\n"  # 0-based [1000, 2000)


def test_parse_confident_regions_skips_comments_and_extra_columns() -> None:
    bed = "# header\ntrack name=x\nchr20\t1000\t2000\tregionA\t0\t+\nchr1\t5\t9\n"
    regions = parse_confident_regions(bed)
    assert len(regions) == 2
    assert (regions[0].chrom, regions[0].start, regions[0].end) == ("chr20", 1000, 2000)


def test_variant_calls_explode_multiallelic_and_skip_symbolic() -> None:
    vcf = (
        "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t100\t.\tA\tG,T\t.\tPASS\t.\n"  # multi-allelic -> 2 calls
        "chr1\t200\t.\tA\t<DEL>\t.\tPASS\t.\n"  # symbolic -> skipped
        "chr1\t300\t.\tA\t*\t.\tPASS\t.\n"  # spanning deletion -> skipped
    )
    calls = variant_calls_from_vcf_text(vcf)
    assert len(calls) == 2
    assert {c.alt for c in calls} == {"G", "T"}


def test_score_giab_stratifies_and_restricts_to_regions() -> None:
    result = score_giab(_CALLED_VCF, _TRUTH_VCF, _BED)
    by = {m.variant_class: m for m in result.by_class}
    # SNV: truth in-region {1001, 1500}; called in-region {1001, 1500, 1700} -> TP2 FP1 FN0
    assert (by["SNV"].tp, by["SNV"].fp, by["SNV"].fn) == (2, 1, 0)
    assert by["SNV"].recall == 1.0
    assert round(by["SNV"].precision, 4) == round(2 / 3, 4)
    # INDEL: truth {1600 AC>A}; called none -> TP0 FP0 FN1
    assert (by["INDEL"].tp, by["INDEL"].fp, by["INDEL"].fn) == (0, 0, 1)
    # ALL: TP2 FP1 FN1
    assert (by["ALL"].tp, by["ALL"].fp, by["ALL"].fn) == (2, 1, 1)
    # The out-of-region truth SNV at 5000 is excluded from the denominator.
    assert result.n_truth_in_regions == 3
    assert "haplotype-aware" in result.caveat


def test_build_command_has_mounts_flags_and_regions(monkeypatch) -> None:
    monkeypatch.setattr(settings, "deepvariant_docker_image", "google/deepvariant@sha256:abc")
    cmd = build_command(
        settings,
        ref_host="/data/ref/GRCh38.fa",
        reads_host="/data/reads/hg002.bam",
        output_dir_host="/data/out",
        out_vcf_name="calls.vcf.gz",
        regions="chr20",
    )
    joined = " ".join(cmd)
    assert "google/deepvariant@sha256:abc" in cmd
    assert "run_deepvariant" in joined
    assert "--ref=/ref/GRCh38.fa" in cmd
    assert "--reads=/reads/hg002.bam" in cmd
    assert "--output_vcf=/output/calls.vcf.gz" in cmd
    assert "--regions=chr20" in cmd
    assert "-v" in cmd


def test_build_command_refuses_without_image(monkeypatch) -> None:
    monkeypatch.setattr(settings, "deepvariant_docker_image", "")
    with pytest.raises(DeepVariantUnavailable, match="DEEPVARIANT_DOCKER_IMAGE"):
        build_command(settings, ref_host="r.fa", reads_host="x.bam", output_dir_host="/o", out_vcf_name="c.vcf.gz")


def test_run_giab_refuses_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "deepvariant_enabled", False)
    with pytest.raises(GiabUnavailable, match="disabled"):
        run_giab_benchmark(settings=settings)


def test_run_giab_refuses_when_inputs_unstaged(monkeypatch) -> None:
    monkeypatch.setattr(settings, "deepvariant_enabled", True)
    monkeypatch.setattr(settings, "giab_reference_path", "")
    with pytest.raises(GiabUnavailable, match="not staged"):
        run_giab_benchmark(settings=settings)


def test_run_giab_benchmark_with_mock_caller(monkeypatch, tmp_path) -> None:
    truth = tmp_path / "truth.vcf"
    bed = tmp_path / "conf.bed"
    called = tmp_path / "calls.vcf"
    truth.write_text(_TRUTH_VCF, encoding="utf-8")
    bed.write_text(_BED, encoding="utf-8")
    called.write_text(_CALLED_VCF, encoding="utf-8")

    monkeypatch.setattr(settings, "deepvariant_enabled", True)
    monkeypatch.setattr(settings, "deepvariant_docker_image", "google/deepvariant@sha256:abc")
    monkeypatch.setattr(settings, "giab_reference_path", str(tmp_path / "ref.fa"))
    monkeypatch.setattr(settings, "giab_reference_build", "GRCh38.test")
    monkeypatch.setattr(settings, "giab_reads_path", str(tmp_path / "hg002.bam"))
    monkeypatch.setattr(settings, "giab_truth_vcf_path", str(truth))
    monkeypatch.setattr(settings, "giab_confident_bed_path", str(bed))
    monkeypatch.setattr(settings, "giab_regions", "chr20")

    result = run_giab_benchmark(settings=settings, caller=lambda s: called)
    assert result.reference_build == "GRCh38.test"
    assert result.regions == "chr20"
    assert "DeepVariant" in result.caller
    by = {m.variant_class: m for m in result.concordance.by_class}
    assert (by["SNV"].tp, by["SNV"].fp, by["SNV"].fn) == (2, 1, 0)


# --- QUAL calibration wiring (the §13 calibration arm going live) --------------------

# Truth: two in-region SNVs. Called: those two at high QUAL (TP) + two FPs, one high one low QUAL.
_TRUTH_CAL = (
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
    "chr20\t1001\t.\tA\tG\t.\tPASS\t.\n"
    "chr20\t1500\t.\tC\tT\t.\tPASS\t.\n"
)
_CALLED_CAL = (
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
    "chr20\t1001\t.\tA\tG\t30\tPASS\t.\n"  # TP, P~0.999
    "chr20\t1500\t.\tC\tT\t30\tPASS\t.\n"  # TP, P~0.999
    "chr20\t1700\t.\tG\tA\t30\tPASS\t.\n"  # FP, P~0.999 (overconfident)
    "chr20\t1800\t.\tT\tC\t.\tPASS\t.\n"  # no QUAL -> excluded from calibration
)
_BED_CAL = "chr20\t1000\t2000\n"


def test_qualified_calls_parse_qual_and_skip_missing() -> None:
    calls = qualified_calls_from_vcf_text(_CALLED_CAL)
    # The '.' QUAL row at 1800 is excluded; the other three carry QUAL 30.
    assert len(calls) == 3
    assert all(c.qual == 30.0 for c in calls)


def test_giab_quality_calibration_curve() -> None:
    curve = giab_quality_calibration(_CALLED_CAL, _TRUTH_CAL, _BED_CAL)
    assert curve is not None
    # 3 QUAL-carrying in-region calls, all P~0.999; 2 of 3 are true positives.
    assert curve.n == 3
    top = curve.bins[-1]
    assert top.observed_freq == pytest.approx(2 / 3, abs=1e-9)


def test_giab_quality_calibration_none_when_too_few() -> None:
    one_call = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\nchr20\t1001\t.\tA\tG\t30\tPASS\t.\n"
    assert giab_quality_calibration(one_call, _TRUTH_CAL, _BED_CAL) is None


def test_run_giab_benchmark_includes_calibration(monkeypatch, tmp_path) -> None:
    truth = tmp_path / "truth.vcf"
    bed = tmp_path / "conf.bed"
    called = tmp_path / "calls.vcf"
    truth.write_text(_TRUTH_CAL, encoding="utf-8")
    bed.write_text(_BED_CAL, encoding="utf-8")
    called.write_text(_CALLED_CAL, encoding="utf-8")
    monkeypatch.setattr(settings, "deepvariant_enabled", True)
    monkeypatch.setattr(settings, "deepvariant_docker_image", "google/deepvariant@sha256:abc")
    monkeypatch.setattr(settings, "giab_reference_path", str(tmp_path / "ref.fa"))
    monkeypatch.setattr(settings, "giab_reference_build", "GRCh38.test")
    monkeypatch.setattr(settings, "giab_reads_path", str(tmp_path / "hg002.bam"))
    monkeypatch.setattr(settings, "giab_truth_vcf_path", str(truth))
    monkeypatch.setattr(settings, "giab_confident_bed_path", str(bed))
    monkeypatch.setattr(settings, "giab_regions", "chr20")

    result = run_giab_benchmark(settings=settings, caller=lambda s: called)
    assert result.calibration is not None
    assert result.calibration.n == 3
