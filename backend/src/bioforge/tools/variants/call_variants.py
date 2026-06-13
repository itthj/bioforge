"""`call_variants` -- run DeepVariant over an aligned BAM and return the called variants.

This turns the §13 benchmark caller (benchmarks/deepvariant_runner) into a first-class agent
tool: the variant tools were annotation-only (ClinVar/dbSNP/gnomAD lookups, HGVS) -- there was no
way to CALL variants on the user's own reads. Now there is.

Honesty posture (matches the other out-of-process models):
  * Gated on BIOFORGE_DEEPVARIANT_ENABLED + a digest-pinned image. With the flag off (default) the
    tool REFUSES with setup guidance -- it never fabricates calls.
  * The reference BUILD is a REQUIRED input and is echoed in every result (section 10: a variant
    without its reference build is meaningless / dangerous). It is never assumed.
  * DeepVariant is heavy (minutes-hours) and reads/writes real files, so it is marked
    `cost_hint="expensive"` + `destructive=False` -- the agent's approval gate applies.

Testability: the underlying `call_variants_impl` takes an injected `run_fn` + `settings`, so the
full path (argv build -> VCF parse -> summary) is unit-tested without Docker or a real caller.
"""

from __future__ import annotations

import asyncio
import gzip
import os
import tempfile
from pathlib import Path

from pydantic import Field

from bioforge.benchmarks.deepvariant_runner import (
    DeepVariantError,
    DeepVariantUnavailable,
    RunFn,
    run_caller,
)
from bioforge.config import Settings, settings
from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool


class CalledVariant(ToolOutput):
    chrom: str
    pos: int
    ref: str
    alt: str
    qual: float | None = None
    variant_class: str  # "SNV" | "INDEL"


class CallVariantsInput(ToolInput):
    reads_path: str = Field(description="Host path to an aligned, indexed BAM (a .bai must sit beside it).")
    reference_path: str = Field(description="Host path to the reference FASTA (a .fai must sit beside it).")
    reference_build: str = Field(
        description="The reference build the reads were aligned to, e.g. 'GRCh38.p14'. REQUIRED and echoed "
        "in the result -- a variant call without its build is meaningless."
    )
    regions: str = Field(
        default="",
        description="Optional region restriction, e.g. 'chr20' or 'chr20:1-10000000'. Empty = whole genome.",
    )
    max_variants_returned: int = Field(
        default=50, ge=1, le=1000, description="How many called variants to include inline (the rest are counted)."
    )


class CallVariantsOutput(ToolOutput):
    caller: str = Field(description="The caller + image used (provenance).")
    reference_build: str
    regions: str
    n_variants: int
    n_snv: int
    n_indel: int
    variants: list[CalledVariant] = Field(description="Up to max_variants_returned called variants.")
    vcf_path: str = Field(description="Host path to the full emitted VCF.")
    truncated: bool = Field(description="True if more variants were called than were returned inline.")


def _classify(ref: str, alt: str) -> str:
    return "SNV" if len(ref) == 1 and len(alt) == 1 else "INDEL"


def _parse_vcf(path: Path, *, limit: int) -> tuple[int, int, int, list[CalledVariant]]:
    """Read a (optionally bgzipped) VCF; return (n_total, n_snv, n_indel, first `limit` variants).

    Multi-allelic ALTs are exploded; symbolic/breakend ALTs are skipped. bgzip is gzip-readable, so
    a plain `gzip.open` handles both .vcf and .vcf.gz transparently (sniffed by the magic bytes)."""
    opener = gzip.open if _is_gzip(path) else open
    n_total = n_snv = n_indel = 0
    sample: list[CalledVariant] = []
    with opener(path, "rt", encoding="utf-8") as fh:  # type: ignore[operator]
        for line in fh:
            if not line or line.startswith("#"):
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 5:
                continue
            chrom, pos_s, _id, ref, alt_field = cols[0], cols[1], cols[2], cols[3].upper(), cols[4].upper()
            qual: float | None = None
            if len(cols) >= 6 and cols[5] not in (".", ""):
                try:
                    qual = float(cols[5])
                except ValueError:
                    qual = None
            for alt in alt_field.split(","):
                if not alt or alt.startswith("<") or "[" in alt or "]" in alt or alt == "*":
                    continue  # symbolic / breakend / spanning deletion -- not a precise allele
                vclass = _classify(ref, alt)
                n_total += 1
                if vclass == "SNV":
                    n_snv += 1
                else:
                    n_indel += 1
                if len(sample) < limit:
                    sample.append(
                        CalledVariant(chrom=chrom, pos=int(pos_s), ref=ref, alt=alt, qual=qual, variant_class=vclass)
                    )
    return n_total, n_snv, n_indel, sample


def _is_gzip(path: Path) -> bool:
    with open(path, "rb") as fh:
        return fh.read(2) == b"\x1f\x8b"


def _require_sibling(path: str, suffixes: tuple[str, ...], what: str) -> None:
    p = Path(path)
    if not p.exists():
        raise ToolError(f"{what} not found at {path!r}. Provide a host path the backend can read.")
    if not any(Path(path + s).exists() for s in suffixes):
        raise ToolError(
            f"{what} at {path!r} is missing its index ({' or '.join(suffixes)}). "
            f"Index it first (e.g. `samtools index` for a BAM, `samtools faidx` for a FASTA)."
        )


def call_variants_impl(
    inp: CallVariantsInput,
    *,
    s: Settings,
    run_fn: RunFn | None = None,
    output_dir: str | None = None,
) -> CallVariantsOutput:
    """Testable core: gate, validate inputs, run the caller, parse + summarize the VCF."""
    if not s.deepvariant_enabled:
        raise ToolError(
            "Variant calling is gated behind BIOFORGE_DEEPVARIANT_ENABLED=true (it is off by "
            "default). Enable it and set BIOFORGE_DEEPVARIANT_DOCKER_IMAGE to a digest-pinned "
            "DeepVariant image (e.g. google/deepvariant:1.6.1). Until then variants are not called "
            "-- they are not faked. See docs/READINESS.md."
        )
    if not s.deepvariant_docker_image:
        raise ToolError(
            "BIOFORGE_DEEPVARIANT_ENABLED is on but BIOFORGE_DEEPVARIANT_DOCKER_IMAGE is unset. "
            "Set it to a digest-pinned DeepVariant image (e.g. google/deepvariant:1.6.1)."
        )
    if not inp.reference_build.strip():
        raise ToolError("reference_build is required -- a variant call without its reference build is meaningless.")

    _require_sibling(inp.reads_path, (".bai",), "Reads BAM")
    _require_sibling(inp.reference_path, (".fai",), "Reference FASTA")

    out_dir = output_dir or tempfile.mkdtemp(prefix="bioforge_calls_")
    os.makedirs(out_dir, exist_ok=True)
    out_vcf_name = "calls.vcf.gz"

    try:
        vcf_path = run_caller(
            s,
            ref_host=inp.reference_path,
            reads_host=inp.reads_path,
            output_dir_host=out_dir,
            out_vcf_name=out_vcf_name,
            regions=inp.regions,
            run_fn=run_fn,
        )
    except DeepVariantUnavailable as e:
        raise ToolError(f"DeepVariant is not usable: {e}") from e
    except DeepVariantError as e:
        raise ToolError(f"DeepVariant failed: {e}") from e

    n_total, n_snv, n_indel, sample = _parse_vcf(Path(vcf_path), limit=inp.max_variants_returned)

    return CallVariantsOutput(
        caller=f"DeepVariant {s.deepvariant_docker_image} model_type={s.deepvariant_model_type}",
        reference_build=inp.reference_build,
        regions=inp.regions or "(whole genome)",
        n_variants=n_total,
        n_snv=n_snv,
        n_indel=n_indel,
        variants=sample,
        vcf_path=str(vcf_path),
        truncated=n_total > len(sample),
    )


@register_tool(
    name="call_variants",
    description=(
        "Call genetic variants (SNVs + indels) from an aligned, indexed BAM using DeepVariant "
        "(Poplin 2018, BSD-3-Clause), returning the called variants + counts. Use when the user "
        "wants to CALL variants from their own sequencing reads -- distinct from annotate_variant / "
        "lookup_clinvar, which interpret an ALREADY-KNOWN variant. REQUIRES the reference BUILD as "
        "an explicit input (never assumed) and a digest-pinned DeepVariant image; it is opt-in "
        "(BIOFORGE_DEEPVARIANT_ENABLED) and refuses with setup guidance when not configured -- it "
        "never fabricates calls. Heavy (minutes-hours): the run goes through the approval gate."
    ),
    input_model=CallVariantsInput,
    output_model=CallVariantsOutput,
    version="1.0.0",
    citations=[
        "Poplin R et al. (2018) A universal SNP and small-indel variant caller using deep neural networks. Nat Biotechnol 36:983-987",
    ],
    cost_hint="expensive",
    destructive=False,
    tags=["variants", "calling", "ngs"],
    model_versions={"call": "deepvariant-run_deepvariant"},
    emits_instance_uncertainty={"call": True},  # DeepVariant emits per-call QUAL (a probability)
    published_accuracy={
        "call": (
            "VERIFY: DeepVariant (Poplin 2018) reports >99.9% SNP / ~99% indel F1 on the GIAB "
            "HG002 truth set; the achieved accuracy on YOUR data depends on coverage, the model_type, "
            "and the reference build -- run the §13 GIAB benchmark for a number you can defend."
        )
    },
    training_distribution={
        "note": "DeepVariant is trained on human GIAB samples; model_type (WGS/WES/PACBIO/ONT) must "
        "match the assay. State the reference build explicitly -- it is a required input.",
    },
)
async def call_variants(inp: CallVariantsInput) -> CallVariantsOutput:
    # DeepVariant is a blocking subprocess; run it off the event loop.
    return await asyncio.to_thread(call_variants_impl, inp, s=settings)
