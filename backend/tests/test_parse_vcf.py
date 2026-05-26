"""Tests for parse_vcf.

Constructed VCF text blobs with known content — easier to verify than real callset
files, no flakiness from upstream record numbering. The validation tests pin the
parser's behavior on lines that break the strict grammar but should still parse.
"""

from __future__ import annotations

import pydantic
import pytest
from bioforge.tools.sequence.parse_vcf import ParseVcfInput, parse_vcf

_MINIMAL_VCF = """##fileformat=VCFv4.2
##INFO=<ID=DP,Number=1,Type=Integer,Description="Total depth">
##INFO=<ID=AF,Number=A,Type=Float,Description="Allele frequency">
##INFO=<ID=DB,Number=0,Type=Flag,Description="dbSNP membership">
##FILTER=<ID=PASS,Description="All filters passed">
##FILTER=<ID=q10,Description="Quality below 10">
##contig=<ID=chr1,length=248956422>
##contig=<ID=chr2,length=242193529>
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO
chr1\t100\trs123\tA\tG\t99.5\tPASS\tDP=42;AF=0.5;DB
chr1\t200\t.\tATCG\tA\t60.0\tPASS\tDP=30
chr1\t300\t.\tA\tAGG\t55.0\tq10\tDP=15
chr2\t400\trs456\tAT\tGC\t80.0\tPASS\tDP=50;AF=0.8
"""


# --- Validation ----------------------------------------------------------------------


async def test_rejects_empty_input() -> None:
    with pytest.raises(pydantic.ValidationError):
        ParseVcfInput(vcf_text="")


async def test_rejects_non_vcf_text() -> None:
    with pytest.raises(pydantic.ValidationError, match="does not begin with"):
        ParseVcfInput(vcf_text="not a vcf at all")


# --- Header parsing ------------------------------------------------------------------


async def test_parses_fileformat_header() -> None:
    out = await parse_vcf(ParseVcfInput(vcf_text=_MINIMAL_VCF))
    assert out.header.fileformat == "VCFv4.2"


async def test_parses_info_field_definitions() -> None:
    out = await parse_vcf(ParseVcfInput(vcf_text=_MINIMAL_VCF))
    assert "DP" in out.header.info_fields
    assert out.header.info_fields["DP"]["Type"] == "Integer"
    assert out.header.info_fields["AF"]["Type"] == "Float"
    assert out.header.info_fields["DB"]["Type"] == "Flag"


async def test_parses_filter_definitions() -> None:
    out = await parse_vcf(ParseVcfInput(vcf_text=_MINIMAL_VCF))
    assert "PASS" in out.header.filters
    assert "q10" in out.header.filters


async def test_parses_contig_order() -> None:
    out = await parse_vcf(ParseVcfInput(vcf_text=_MINIMAL_VCF))
    assert out.header.contigs == ["chr1", "chr2"]


# --- Record parsing ------------------------------------------------------------------


async def test_parses_all_four_records() -> None:
    out = await parse_vcf(ParseVcfInput(vcf_text=_MINIMAL_VCF))
    assert out.num_records_total == 4
    assert out.num_records_returned == 4


async def test_snv_classification() -> None:
    out = await parse_vcf(ParseVcfInput(vcf_text=_MINIMAL_VCF))
    snv = next(v for v in out.variants if v.chrom == "chr1" and v.pos == 100)
    assert snv.variant_class == "SNV"
    assert snv.ref == "A"
    assert snv.alt == ["G"]
    assert snv.id == "rs123"
    assert snv.qual == 99.5
    assert snv.filter == ["PASS"]


async def test_deletion_classification() -> None:
    out = await parse_vcf(ParseVcfInput(vcf_text=_MINIMAL_VCF))
    del_var = next(v for v in out.variants if v.chrom == "chr1" and v.pos == 200)
    assert del_var.variant_class == "deletion"
    assert del_var.ref == "ATCG"
    assert del_var.alt == ["A"]


async def test_insertion_classification() -> None:
    out = await parse_vcf(ParseVcfInput(vcf_text=_MINIMAL_VCF))
    ins = next(v for v in out.variants if v.chrom == "chr1" and v.pos == 300)
    assert ins.variant_class == "insertion"
    assert ins.alt == ["AGG"]


async def test_mnv_classification() -> None:
    out = await parse_vcf(ParseVcfInput(vcf_text=_MINIMAL_VCF))
    mnv = next(v for v in out.variants if v.chrom == "chr2")
    assert mnv.variant_class == "MNV"
    assert mnv.ref == "AT"
    assert mnv.alt == ["GC"]


async def test_dot_id_becomes_none() -> None:
    out = await parse_vcf(ParseVcfInput(vcf_text=_MINIMAL_VCF))
    record = next(v for v in out.variants if v.pos == 200)
    assert record.id is None


async def test_info_field_type_coercion() -> None:
    out = await parse_vcf(ParseVcfInput(vcf_text=_MINIMAL_VCF))
    snv = next(v for v in out.variants if v.pos == 100)
    # DP is declared Integer
    assert snv.info["DP"] == 42
    assert isinstance(snv.info["DP"], int)
    # AF is declared Number=A (one entry per alt allele) + Type=Float, so even a
    # single-allele site parses as a one-element list — that's correct VCF semantics.
    assert snv.info["AF"] == [0.5]
    assert isinstance(snv.info["AF"][0], float)
    # DB is Flag (no value in record) — present as True
    assert snv.info["DB"] is True


# --- Aggregates ----------------------------------------------------------------------


async def test_counts_by_class() -> None:
    out = await parse_vcf(ParseVcfInput(vcf_text=_MINIMAL_VCF))
    assert out.counts_by_class == {"SNV": 1, "deletion": 1, "insertion": 1, "MNV": 1}


async def test_num_passing_filter() -> None:
    out = await parse_vcf(ParseVcfInput(vcf_text=_MINIMAL_VCF))
    # Three PASS + one q10
    assert out.num_passing_filter == 3


# --- Truncation + error reporting ----------------------------------------------------


async def test_max_records_caps_returned_but_not_counts() -> None:
    out = await parse_vcf(ParseVcfInput(vcf_text=_MINIMAL_VCF, max_records=2))
    assert out.num_records_total == 4  # full input
    assert out.num_records_returned == 2
    assert sum(out.counts_by_class.values()) == 4  # counts cover full input
    # Caveat surfaced
    assert any("max_records cap" in c for c in out.caveats)


async def test_malformed_line_recorded_in_warnings_not_raised() -> None:
    """A line with too few columns should NOT crash the tool; it's a parse warning."""
    bad_vcf = _MINIMAL_VCF + "chr1\t500\tonly-three-cols\n"
    out = await parse_vcf(ParseVcfInput(vcf_text=bad_vcf))
    # The good 4 records still parsed
    assert out.num_records_total == 5
    assert out.num_records_returned == 4  # the bad line didn't yield a variant
    assert any("expected ≥8" in w for w in out.parse_warnings)


# --- Honesty -------------------------------------------------------------------------


async def test_caveats_mention_no_annotation() -> None:
    out = await parse_vcf(ParseVcfInput(vcf_text=_MINIMAL_VCF))
    text = " ".join(out.caveats).lower()
    assert "annotate" in text or "annotation" in text
    assert "clinvar" in text or "vep" in text


async def test_is_registered_with_variant_tags() -> None:
    from bioforge.tools.registry import get_tool

    spec = get_tool("parse_vcf")
    assert spec.cost_hint == "cheap"
    assert "variant" in spec.tags
    assert "vcf" in spec.tags
    assert spec.destructive is False
