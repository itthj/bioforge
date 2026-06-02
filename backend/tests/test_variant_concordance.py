"""§13 GIAB variant-concordance metric -- unit tests. Pure, deterministic, synthetic truth sets."""

from __future__ import annotations

import pytest
from bioforge.benchmarks.variant_concordance import (
    ConfidentRegion,
    VariantCall,
    score_variant_concordance,
    variant_calls_from_parsed,
)


def _by(result, klass):
    return next(m for m in result.by_class if m.variant_class == klass)


# --- normalization -------------------------------------------------------------------------------


def test_snv_key_is_unchanged() -> None:
    assert VariantCall("chr1", 100, "A", "G").normalized_key() == ("chr1", 100, "A", "G")
    assert VariantCall("chr1", 100, "A", "G").is_snv


def test_indel_parsimonious_trim_reconciles_padding() -> None:
    # Same 1bp insertion of T after pos 100, represented two ways.
    a = VariantCall("chr1", 100, "A", "AT")  # already minimal
    b = VariantCall("chr1", 100, "AG", "ATG")  # shares trailing G -> trims to (100,'A','AT')
    assert a.normalized_key() == b.normalized_key() == ("chr1", 100, "A", "AT")
    assert not a.is_snv


def test_indel_prefix_trim_advances_pos() -> None:
    # 'GA'->'G' deletion padded with a shared leading C: (100,'CGA','CG') -> (101,'GA','G')
    v = VariantCall("chr1", 100, "CGA", "CG")
    assert v.normalized_key() == ("chr1", 101, "GA", "G")


# --- region stratification -----------------------------------------------------------------------


def test_variants_outside_confident_regions_are_excluded() -> None:
    regions = [ConfidentRegion("chr1", 0, 200)]  # 0-based [0,200) -> covers 1-based pos 1..200
    called = [VariantCall("chr1", 100, "A", "G"), VariantCall("chr1", 500, "A", "G")]  # 2nd is outside
    truth = [VariantCall("chr1", 100, "A", "G")]
    result = score_variant_concordance(called, truth, regions)
    assert result.n_called_total == 2
    assert result.n_called_in_regions == 1  # the pos-500 call dropped
    snv = _by(result, "SNV")
    assert (snv.tp, snv.fp, snv.fn) == (1, 0, 0)


def test_membership_is_halfopen() -> None:
    regions = [ConfidentRegion("chr1", 99, 100)]  # 0-based [99,100) -> only 1-based pos 100
    inside = VariantCall("chr1", 100, "A", "G")  # start0 = 99 -> in
    outside = VariantCall("chr1", 101, "A", "G")  # start0 = 100 -> out (end exclusive)
    result = score_variant_concordance([inside, outside], [inside, outside], regions)
    assert result.n_called_in_regions == 1
    assert result.n_truth_in_regions == 1


# --- precision / recall / f1 ---------------------------------------------------------------------


def test_perfect_concordance() -> None:
    regions = [ConfidentRegion("chr1", 0, 1000)]
    variants = [VariantCall("chr1", 100, "A", "G"), VariantCall("chr1", 200, "AT", "A")]
    result = score_variant_concordance(variants, variants, regions)
    allm = _by(result, "ALL")
    assert (allm.tp, allm.fp, allm.fn) == (2, 0, 0)
    assert allm.precision == 1.0 and allm.recall == 1.0 and allm.f1 == 1.0


def test_mixed_tp_fp_fn_with_snv_indel_stratification() -> None:
    regions = [ConfidentRegion("chr1", 0, 1000)]
    truth = [
        VariantCall("chr1", 100, "A", "G"),  # SNV -- called (TP)
        VariantCall("chr1", 150, "C", "T"),  # SNV -- missed (FN)
        VariantCall("chr1", 200, "AT", "A"),  # INDEL -- called (TP)
    ]
    called = [
        VariantCall("chr1", 100, "A", "G"),  # SNV TP
        VariantCall("chr1", 120, "A", "C"),  # SNV FP (not in truth)
        VariantCall("chr1", 200, "AT", "A"),  # INDEL TP
        VariantCall("chr1", 300, "G", "GA"),  # INDEL FP
    ]
    result = score_variant_concordance(called, truth, regions)
    snv = _by(result, "SNV")
    indel = _by(result, "INDEL")
    allm = _by(result, "ALL")
    assert (snv.tp, snv.fp, snv.fn) == (1, 1, 1)
    assert snv.precision == 0.5 and snv.recall == 0.5 and snv.f1 == 0.5
    assert (indel.tp, indel.fp, indel.fn) == (1, 1, 0)
    assert indel.precision == 0.5 and indel.recall == 1.0
    # ALL aggregates: tp=2, fp=2, fn=1 -> P=0.5, R=2/3, F1=2*2/(2*2+2+1)=4/7
    assert (allm.tp, allm.fp, allm.fn) == (2, 2, 1)
    assert allm.precision == 0.5
    assert allm.recall == pytest.approx(0.6667, abs=1e-3)
    assert allm.f1 == pytest.approx(0.5714, abs=1e-3)


def test_indel_representation_difference_still_matches_via_normalization() -> None:
    regions = [ConfidentRegion("chr1", 0, 1000)]
    truth = [VariantCall("chr1", 100, "A", "AT")]  # minimal
    called = [VariantCall("chr1", 100, "AG", "ATG")]  # padded, normalizes to the same key
    result = score_variant_concordance(called, truth, regions)
    indel = _by(result, "INDEL")
    assert (indel.tp, indel.fp, indel.fn) == (1, 0, 0)


def test_empty_denominators_are_zero_not_nan() -> None:
    regions = [ConfidentRegion("chr1", 0, 1000)]
    # No calls, no truth -> all metrics 0.0 (documented JSON-safe convention), no NaN.
    result = score_variant_concordance([], [], regions)
    allm = _by(result, "ALL")
    assert allm.precision == 0.0 and allm.recall == 0.0 and allm.f1 == 0.0
    assert result.caveat  # the honesty caveat always travels with the result


def test_caveat_disclaims_haplotype_aware_comparison() -> None:
    result = score_variant_concordance([], [], [ConfidentRegion("chr1", 0, 10)])
    assert "hap.py" in result.caveat
    assert "not the haplotype-aware" in result.caveat.lower()


# --- parse_vcf adapter ---------------------------------------------------------------------------


class _FakeParsed:
    def __init__(self, chrom, pos, ref, alt):
        self.chrom = chrom
        self.pos = pos
        self.ref = ref
        self.alt = alt


def test_adapter_explodes_multiallelic_and_skips_symbolic() -> None:
    parsed = [
        _FakeParsed("chr1", 100, "A", ["G", "T"]),  # biallelic split -> two calls
        _FakeParsed("chr2", 200, "C", ["<DEL>"]),  # symbolic -> skipped
        _FakeParsed("chr3", 300, "G", ["GA", "*"]),  # one real, one spanning-deletion (skipped)
    ]
    calls = variant_calls_from_parsed(parsed)
    keys = {c.normalized_key() for c in calls}
    assert ("chr1", 100, "A", "G") in keys
    assert ("chr1", 100, "A", "T") in keys
    assert ("chr3", 300, "G", "GA") in keys
    assert len(calls) == 3  # the <DEL> and the * are dropped
