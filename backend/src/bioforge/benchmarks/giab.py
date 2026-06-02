"""§13 GIAB end-to-end concordance benchmark: call variants, then score vs the truth set.

This wires the previously-built SCORING half (`variant_concordance.score_variant_concordance`)
to a real CALLER (DeepVariant, `deepvariant_runner`). The pure pieces -- a VCF-body parser, a BED
parser, and `score_giab` -- are fully unit-testable on small fixtures; `run_giab_benchmark` is the
live path that runs DeepVariant over the configured HG002 reads + GRCh38 reference and scores the
calls against the GIAB truth VCF within its high-confidence BED.

Honesty (rule 18 / §0):
  * The benchmark stays **guard_only** in the Accuracy Report: it needs Docker + the GRCh38
    reference + the HG002 truth set, so it never runs on a page load and is never faked.
  * The concordance metric's own caveat travels with the result (genotype-agnostic exact match,
    NOT haplotype-aware like hap.py).
  * The reference build is recorded, never assumed (§10).
"""

from __future__ import annotations

import gzip
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

from bioforge.benchmarks.deepvariant_runner import run_caller
from bioforge.benchmarks.variant_concordance import (
    ConfidentRegion,
    VariantCall,
    VariantConcordanceResult,
    score_variant_concordance,
)
from bioforge.config import Settings
from bioforge.config import settings as _global_settings


class GiabUnavailable(Exception):
    """Raised when the live GIAB benchmark cannot run (disabled or inputs unstaged)."""


class GiabBenchmarkResult(BaseModel):
    """A GIAB concordance run, with the provenance a scientist needs to trust the number."""

    reference_build: str = Field(description="The USER-CONFIRMED reference build, e.g. 'GRCh38.p14'.")
    regions: str = Field(description="Region restriction passed to the caller (e.g. 'chr20'), or '(all)'.")
    caller: str = Field(
        description="The variant caller + image used, e.g. 'DeepVariant google/deepvariant@sha256:...'."
    )
    concordance: VariantConcordanceResult


def _read_text(path: str | Path) -> str:
    """Read a VCF/BED file, transparently decompressing .gz."""
    p = Path(path)
    if p.suffix == ".gz":
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            return fh.read()
    return p.read_text(encoding="utf-8")


def parse_confident_regions(bed_text: str) -> list[ConfidentRegion]:
    """Parse a BED3+ file into ConfidentRegions (chrom, 0-based start, exclusive end).

    Skips comment/track/browser lines and blanks. Ignores extra columns beyond the first three.
    """
    regions: list[ConfidentRegion] = []
    for line in bed_text.splitlines():
        s = line.strip()
        if not s or s.startswith(("#", "track", "browser")):
            continue
        parts = s.split("\t") if "\t" in s else s.split()
        if len(parts) < 3:
            continue
        chrom, start, end = parts[0], parts[1], parts[2]
        try:
            regions.append(ConfidentRegion(chrom=chrom, start=int(start), end=int(end)))
        except ValueError:
            continue  # a non-integer coordinate line is skipped, never guessed
    return regions


def variant_calls_from_vcf_text(vcf_text: str) -> list[VariantCall]:
    """Parse a VCF body into VariantCalls, exploding multi-allelic sites and skipping symbolic
    ALTs (<DEL>, <*>, breakends, spanning deletions). Header lines ('#') are ignored."""
    calls: list[VariantCall] = []
    for line in vcf_text.splitlines():
        if not line or line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) < 5:
            continue
        chrom, pos_s, _id, ref, alt_field = cols[0], cols[1], cols[2], cols[3].upper(), cols[4].upper()
        try:
            pos = int(pos_s)
        except ValueError:
            continue
        for alt in alt_field.split(","):
            a = alt.strip()
            if not a or a == "." or a.startswith("<") or "[" in a or "]" in a or a == "*":
                continue
            calls.append(VariantCall(chrom=chrom, pos=pos, ref=ref, alt=a))
    return calls


def score_giab(called_vcf_text: str, truth_vcf_text: str, confident_bed_text: str) -> VariantConcordanceResult:
    """Pure scoring core: parse the three inputs and compute stratified concordance.

    The called + truth VCFs are parsed to VariantCalls; the BED to ConfidentRegions; matching and
    region-restriction are delegated to `score_variant_concordance` (which carries the caveat).
    """
    called = variant_calls_from_vcf_text(called_vcf_text)
    truth = variant_calls_from_vcf_text(truth_vcf_text)
    regions = parse_confident_regions(confident_bed_text)
    return score_variant_concordance(called, truth, regions)


def run_giab_benchmark(*, settings: Settings | None = None, caller=None) -> GiabBenchmarkResult:
    """Live GIAB benchmark: call variants with DeepVariant, then score vs the GIAB truth set.

    Requires `deepvariant_enabled` plus the staged GIAB inputs (reference + build, reads, truth VCF,
    confident BED). `caller` is injectable for tests: a `(Settings) -> Path` returning the called
    VCF; the default runs DeepVariant via `deepvariant_runner.run_caller`. Raises `GiabUnavailable`
    when disabled or any input is unstaged -- it never fabricates a number.
    """
    s = settings or _global_settings
    if not s.deepvariant_enabled:
        raise GiabUnavailable(
            "GIAB benchmark is disabled. Set BIOFORGE_DEEPVARIANT_ENABLED=true, a digest-pinned "
            "DeepVariant image, and the GIAB inputs (reference+build, reads, truth VCF, confident BED)."
        )
    required = {
        "reference path": s.giab_reference_path,
        "reference build": s.giab_reference_build,
        "reads path": s.giab_reads_path,
        "truth VCF path": s.giab_truth_vcf_path,
        "confident BED path": s.giab_confident_bed_path,
    }
    missing = [name for name, val in required.items() if not val]
    if missing:
        raise GiabUnavailable(f"GIAB inputs not staged: {', '.join(missing)}.")

    def _default_caller(ss: Settings) -> Path:
        out_dir = tempfile.mkdtemp(prefix="bioforge_giab_")
        return run_caller(
            ss,
            ref_host=ss.giab_reference_path,
            reads_host=ss.giab_reads_path,
            output_dir_host=out_dir,
            regions=ss.giab_regions,
        )

    run_call = caller if caller is not None else _default_caller
    called_vcf_path = run_call(s)
    result = score_giab(
        _read_text(called_vcf_path),
        _read_text(s.giab_truth_vcf_path),
        _read_text(s.giab_confident_bed_path),
    )
    return GiabBenchmarkResult(
        reference_build=s.giab_reference_build,
        regions=s.giab_regions or "(all)",
        caller=f"DeepVariant {s.deepvariant_docker_image or '(image unset)'} model_type={s.deepvariant_model_type}",
        concordance=result,
    )
