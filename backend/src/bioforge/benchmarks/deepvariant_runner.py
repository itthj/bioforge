"""Out-of-process DeepVariant caller for the section 13 GIAB concordance benchmark.

DeepVariant (Poplin et al. 2018, BSD-3-Clause -- see docs/license_audit.md) runs as the
`run_deepvariant` pipeline inside a digest-pinned image, over an aligned reads BAM + a reference
FASTA, emitting a VCF. There is no pure-Python fallback. `build_command` is pure + unit-testable;
the launch goes through an injectable `run_fn` so tests never spawn Docker.

The reference build is the caller's responsibility to state and record (section 10) -- this module
just runs the configured image over the configured inputs and returns the output VCF path.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from bioforge.config import Settings

# (argv, timeout_seconds) -> (returncode, stdout, stderr)
RunFn = Callable[[list[str], float], tuple[int, str, str]]


class DeepVariantUnavailable(Exception):
    """Raised when the DeepVariant runtime is not usable (disabled or misconfigured)."""


class DeepVariantError(Exception):
    """Raised when the DeepVariant subprocess fails."""


def build_command(
    s: Settings,
    *,
    ref_host: str,
    reads_host: str,
    output_dir_host: str,
    out_vcf_name: str,
    regions: str = "",
) -> list[str]:
    """Construct the `docker run ... run_deepvariant` argv. Pure + testable.

    Mounts the reference dir, the reads dir, and the output dir to fixed container paths and
    references the files there. The reference's `.fai` and the reads' `.bai` must already sit
    alongside their files in those dirs (the orchestrator guarantees this). Raises
    `DeepVariantUnavailable` when the image is unset.
    """
    if not s.deepvariant_docker_image:
        raise DeepVariantUnavailable(
            "BIOFORGE_DEEPVARIANT_DOCKER_IMAGE is unset. Provide a digest-pinned DeepVariant image "
            "(e.g. google/deepvariant@sha256:...) -- see benchmarks/giab + docs/license_audit.md."
        )
    ref_dir, ref_name = os.path.split(os.path.abspath(ref_host))
    reads_dir, reads_name = os.path.split(os.path.abspath(reads_host))
    out_dir = os.path.abspath(output_dir_host)
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{ref_dir}:/ref",
        "-v",
        f"{reads_dir}:/reads",
        "-v",
        f"{out_dir}:/output",
        s.deepvariant_docker_image,
        "/opt/deepvariant/bin/run_deepvariant",
        f"--model_type={s.deepvariant_model_type}",
        f"--ref=/ref/{ref_name}",
        f"--reads=/reads/{reads_name}",
        f"--output_vcf=/output/{out_vcf_name}",
        f"--num_shards={s.deepvariant_num_shards}",
    ]
    if regions:
        cmd.append(f"--regions={regions}")
    return cmd


def _default_run_fn(argv: list[str], timeout: float) -> tuple[int, str, str]:
    import subprocess

    try:
        proc = subprocess.run(  # noqa: S603 — argv built from validated settings, not user text
            argv, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as e:
        raise DeepVariantUnavailable(f"Runner executable {argv[0]!r} not found. Is Docker installed? {e}") from e
    except subprocess.TimeoutExpired as e:
        raise DeepVariantError(f"DeepVariant timed out after {timeout}s.") from e
    return proc.returncode, proc.stdout, proc.stderr


def run_caller(
    s: Settings,
    *,
    ref_host: str,
    reads_host: str,
    output_dir_host: str,
    out_vcf_name: str = "calls.vcf.gz",
    regions: str = "",
    run_fn: RunFn | None = None,
) -> Path:
    """Run DeepVariant and return the path to the emitted VCF (on the host).

    Raises `DeepVariantUnavailable` (image unset / Docker missing) or `DeepVariantError`
    (nonzero exit, or the expected output VCF was not produced).
    """
    run = run_fn if run_fn is not None else _default_run_fn
    argv = build_command(
        s,
        ref_host=ref_host,
        reads_host=reads_host,
        output_dir_host=output_dir_host,
        out_vcf_name=out_vcf_name,
        regions=regions,
    )
    code, _stdout, stderr = run(argv, s.deepvariant_timeout_seconds)
    if code != 0:
        raise DeepVariantError(f"DeepVariant exited {code}. stderr tail: {stderr[-2000:]!r}")
    out_path = Path(os.path.abspath(output_dir_host)) / out_vcf_name
    if not out_path.exists():
        raise DeepVariantError(f"DeepVariant reported success but {out_path} is missing.")
    return out_path
