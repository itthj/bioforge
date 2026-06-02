"""Out-of-process runner for MAFFT multiple-sequence alignment.

MAFFT reads a FASTA on stdin and writes the aligned FASTA to stdout, so -- unlike the ML
scorers, which need a wrapper script baked into the image -- we invoke MAFFT NATIVELY; no
custom protocol. Two backends via `settings.mafft_runner`: **docker** (a digest-pinned
CORE-ONLY MAFFT image; the bundled extensions are restrictively licensed and excluded) and
**local** (`settings.mafft_binary` on PATH). `build_command` is pure + unit-testable; the
launch goes through an injectable `run_fn` so tests never spawn a subprocess.
"""

from __future__ import annotations

from collections.abc import Callable

from bioforge.config import Settings

# (argv, stdin_text, timeout_seconds) -> stdout_text
RunFn = Callable[[list[str], str, float], str]

# MAFFT flags: --auto picks the strategy by input size; --quiet keeps progress off stderr;
# the trailing '-' tells MAFFT to read the input FASTA from stdin (output goes to stdout).
_MAFFT_ARGS = ["--auto", "--quiet", "-"]


class MafftUnavailable(Exception):
    """Raised when the out-of-process MAFFT runtime is not usable (disabled or misconfigured)."""


class MafftError(Exception):
    """Raised when the MAFFT subprocess fails or returns unusable output."""


def build_command(s: Settings) -> list[str]:
    """Construct the subprocess argv for the configured backend. Pure + testable.

    Raises `MafftUnavailable` when the backend is misconfigured (missing image or binary,
    unknown runner) so the tool boundary can surface actionable setup guidance.
    """
    runner = (s.mafft_runner or "docker").lower()
    if runner == "docker":
        if not s.mafft_docker_image:
            raise MafftUnavailable(
                "mafft_runner='docker' but BIOFORGE_MAFFT_DOCKER_IMAGE is unset. Provide a "
                "digest-pinned CORE-ONLY MAFFT image (see models/mafft/legacy/README.md) -- the "
                "bundled MAFFT extensions are restrictively licensed and must not be included."
            )
        return ["docker", "run", "--rm", "-i", s.mafft_docker_image, "mafft", *_MAFFT_ARGS]
    if runner == "local":
        if not s.mafft_binary:
            raise MafftUnavailable("mafft_runner='local' but BIOFORGE_MAFFT_BINARY is unset.")
        return [s.mafft_binary, *_MAFFT_ARGS]
    raise MafftUnavailable(f"Unknown mafft_runner {runner!r}; expected 'docker' or 'local'.")


def _default_run_fn(argv: list[str], stdin_text: str, timeout: float) -> str:
    import subprocess

    try:
        proc = subprocess.run(  # noqa: S603 — argv built from validated settings, not user text
            argv, input=stdin_text, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as e:
        raise MafftUnavailable(
            f"Runner executable {argv[0]!r} not found. Is Docker (or the mafft binary) installed? {e}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise MafftError(f"MAFFT subprocess timed out after {timeout}s.") from e
    if proc.returncode != 0:
        raise MafftError(f"MAFFT subprocess exited {proc.returncode}. stderr tail: {proc.stderr[-2000:]!r}")
    return proc.stdout


def run_alignment(fasta_in: str, s: Settings, *, run_fn: RunFn | None = None) -> str:
    """Send `fasta_in` to the MAFFT backend and return the aligned FASTA (stdout).

    Raises `MafftUnavailable` (backend missing/misconfigured) or `MafftError` (nonzero exit,
    timeout, or empty output). Parsing + biological soundness checks are the caller's job.
    """
    run = run_fn if run_fn is not None else _default_run_fn
    argv = build_command(s)
    stdout = run(argv, fasta_in, s.mafft_timeout_seconds)
    if not stdout.strip():
        raise MafftError("MAFFT returned empty output.")
    return stdout
