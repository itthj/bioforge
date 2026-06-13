"""Tests for the call_variants tool (DeepVariant wrapper).

No Docker / no real caller: an injected run_fn writes a synthetic VCF so the gate, input
validation, VCF parsing (incl. gzip + multi-allelic + symbolic skip), and provenance are all
covered hermetically.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest
from bioforge.config import Settings
from bioforge.tools.base import ToolError
from bioforge.tools.variants.call_variants import CallVariantsInput, call_variants_impl

_VCF_BODY = (
    "##fileformat=VCFv4.2\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
    "chr20\t100\t.\tA\tG\t30.0\tPASS\t.\n"  # SNV
    "chr20\t200\t.\tAC\tA\t25.5\tPASS\t.\n"  # INDEL (deletion)
    "chr20\t300\t.\tC\tT,G\t40.0\tPASS\t.\n"  # multi-allelic -> 2 SNVs
    "chr20\t400\t.\tG\t<DEL>\t10.0\tPASS\t.\n"  # symbolic -> skipped
)


def _settings(**over) -> Settings:
    # pydantic-settings init is by ALIAS, so field-name kwargs are ignored; mutate after build
    # (the same way the rest of the suite monkeypatches settings).
    s = Settings()
    s.deepvariant_enabled = True
    s.deepvariant_docker_image = "google/deepvariant:1.6.1"
    for k, v in over.items():
        setattr(s, k, v)
    return s


def _make_inputs(tmp_path: Path) -> CallVariantsInput:
    reads = tmp_path / "reads.bam"
    reads.write_bytes(b"BAM\x01")
    (tmp_path / "reads.bam.bai").write_bytes(b"BAI\x01")
    ref = tmp_path / "ref.fa"
    ref.write_text(">chr20\nACGT\n")
    (tmp_path / "ref.fa.fai").write_text("chr20\t4\t6\t4\t5\n")
    return CallVariantsInput(
        reads_path=str(reads), reference_path=str(ref), reference_build="GRCh38.p14", regions="chr20"
    )


def _writing_run_fn(out_dir: Path, gzip_out: bool):
    """A run_fn that writes the synthetic VCF where run_caller expects it (out_dir/calls.vcf.gz)."""

    def run_fn(argv: list[str], timeout: float):
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / "calls.vcf.gz"
        if gzip_out:
            with gzip.open(target, "wt", encoding="utf-8") as fh:
                fh.write(_VCF_BODY)
        else:
            target.write_text(_VCF_BODY, encoding="utf-8")
        return 0, "ok", ""

    return run_fn


@pytest.mark.parametrize("gzip_out", [False, True])
def test_call_variants_parses_and_summarizes(tmp_path: Path, gzip_out: bool) -> None:
    out = call_variants_impl(
        _make_inputs(tmp_path),
        s=_settings(),
        run_fn=_writing_run_fn(tmp_path / "out", gzip_out),
        output_dir=str(tmp_path / "out"),
    )
    # 1 SNV + 1 INDEL + 2 SNV (multi-allelic) = 4 precise alleles; <DEL> skipped.
    assert out.n_variants == 4
    assert out.n_snv == 3
    assert out.n_indel == 1
    assert out.reference_build == "GRCh38.p14"
    assert "deepvariant" in out.caller.lower()
    assert out.regions == "chr20"
    # First call carries its QUAL.
    assert out.variants[0].qual == 30.0
    assert out.variants[0].variant_class == "SNV"


def test_truncation_flag(tmp_path: Path) -> None:
    inp = _make_inputs(tmp_path)
    inp.max_variants_returned = 2
    out = call_variants_impl(
        inp, s=_settings(), run_fn=_writing_run_fn(tmp_path / "out", False), output_dir=str(tmp_path / "out")
    )
    assert len(out.variants) == 2
    assert out.truncated is True
    assert out.n_variants == 4


def test_refuses_when_disabled(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="BIOFORGE_DEEPVARIANT_ENABLED"):
        call_variants_impl(
            _make_inputs(tmp_path),
            s=_settings(deepvariant_enabled=False),
            run_fn=_writing_run_fn(tmp_path / "out", False),
        )


def test_refuses_when_image_unset(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="DOCKER_IMAGE"):
        call_variants_impl(
            _make_inputs(tmp_path),
            s=_settings(deepvariant_docker_image=""),
            run_fn=_writing_run_fn(tmp_path / "out", False),
        )


def test_missing_bam_index_is_caught(tmp_path: Path) -> None:
    inp = _make_inputs(tmp_path)
    (tmp_path / "reads.bam.bai").unlink()  # remove the index
    with pytest.raises(ToolError, match="missing its index"):
        call_variants_impl(
            inp, s=_settings(), run_fn=_writing_run_fn(tmp_path / "out", False), output_dir=str(tmp_path / "out")
        )


def test_missing_reference_build_rejected(tmp_path: Path) -> None:
    inp = _make_inputs(tmp_path)
    inp.reference_build = "  "
    with pytest.raises(ToolError, match="reference_build is required"):
        call_variants_impl(
            inp, s=_settings(), run_fn=_writing_run_fn(tmp_path / "out", False), output_dir=str(tmp_path / "out")
        )


def test_registered_in_tool_registry() -> None:
    import bioforge.tools  # noqa: F401
    from bioforge.tools.registry import list_tools

    assert any(t.name == "call_variants" for t in list_tools())
