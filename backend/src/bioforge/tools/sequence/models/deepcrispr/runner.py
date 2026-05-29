"""Out-of-process runner for DeepCRISPR inference.

DeepCRISPR is TensorFlow 1.3 / Python 3.6 and cannot import into this interpreter,
so we shell out to a pinned legacy environment and talk to it over a tiny JSON
protocol on stdin/stdout:

    stdin :  {"guides": ["<23bp>", ...], "model": "ontar_cnn_reg_seq"}
    stdout:  {"model": "...", "scores": [0.73, ...]}   (or {"error": "..."})

Two interchangeable backends, selected by `settings.deepcrispr_runner`:

* **docker** (default): runs a digest-pinned tf1.3/py3.6 image. The fetched model
  dir is bind-mounted read-only; the legacy script is baked into the image.
* **local**: runs `settings.deepcrispr_python` (a conda env with python3.6 +
  tensorflow==1.3.0 + sonnet==1.9) against the legacy script shipped here.

`build_command` is a pure function (unit-testable). The actual process launch goes
through an injectable `run_fn` so tests never spawn a real subprocess.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from bioforge.config import Settings
from bioforge.tools.sequence.models.deepcrispr.fetcher import (
    DeepCRISPRPaths,
    DeepCRISPRUnavailable,
)

# (argv, stdin_text, timeout_seconds) -> stdout_text
RunFn = Callable[[list[str], str, float], str]

# The legacy-runtime script, shipped alongside this package. The `local` backend
# invokes it directly; the docker image bakes a copy in at build time.
_LEGACY_SCRIPT = Path(__file__).parent / "legacy" / "deepcrispr_infer.py"
_CONTAINER_SCRIPT = "/opt/deepcrispr/deepcrispr_infer.py"
_CONTAINER_MODELS_MOUNT = "/models"


class DeepCRISPRInferenceError(Exception):
    """Raised when the legacy subprocess fails or returns an unexpected payload."""


def build_command(paths: DeepCRISPRPaths, s: Settings) -> list[str]:
    """Construct the subprocess argv for the configured backend. Pure + testable.

    Raises `DeepCRISPRUnavailable` when the backend is misconfigured (missing image
    or python path, unknown runner) — that surfaces as actionable setup guidance.
    """
    runner = (s.deepcrispr_runner or "docker").lower()
    if runner == "docker":
        if not s.deepcrispr_docker_image:
            raise DeepCRISPRUnavailable(
                "deepcrispr_runner='docker' but BIOFORGE_DEEPCRISPR_DOCKER_IMAGE is unset. "
                "Build the legacy image (see models/deepcrispr/legacy/Dockerfile), then set the "
                "var to its digest-pinned reference (e.g. 'bioforge/deepcrispr@sha256:...')."
            )
        return [
            "docker",
            "run",
            "--rm",
            "-i",
            "-v",
            f"{paths.data_dir}:{_CONTAINER_MODELS_MOUNT}:ro",
            s.deepcrispr_docker_image,
            "python",
            _CONTAINER_SCRIPT,
            "--model-dir",
            f"{_CONTAINER_MODELS_MOUNT}/{paths.model_dir.name}",
        ]
    if runner == "local":
        if not s.deepcrispr_python:
            raise DeepCRISPRUnavailable(
                "deepcrispr_runner='local' but BIOFORGE_DEEPCRISPR_PYTHON is unset. "
                "Create the conda env (see models/deepcrispr/legacy/environment.yml) and set the "
                "var to that interpreter (e.g. '~/miniconda3/envs/deepcrispr/bin/python')."
            )
        return [
            s.deepcrispr_python,
            str(_LEGACY_SCRIPT),
            "--model-dir",
            str(paths.model_dir),
        ]
    raise DeepCRISPRUnavailable(f"Unknown deepcrispr_runner {runner!r}; expected 'docker' or 'local'.")


def _default_run_fn(argv: list[str], stdin_text: str, timeout: float) -> str:
    """Launch the subprocess for real. Translates process failures into typed errors."""
    import subprocess

    try:
        proc = subprocess.run(  # noqa: S603 — argv is built from validated settings, not user text
            argv,
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise DeepCRISPRUnavailable(
            f"Runner executable {argv[0]!r} not found. Is Docker (or the configured conda "
            f"python) installed and on PATH? Underlying error: {e}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise DeepCRISPRInferenceError(
            f"DeepCRISPR subprocess timed out after {timeout}s. Raise BIOFORGE_DEEPCRISPR_TIMEOUT_SECONDS "
            "for large batches or a cold model load."
        ) from e
    if proc.returncode != 0:
        raise DeepCRISPRInferenceError(
            f"DeepCRISPR subprocess exited {proc.returncode}. stderr tail: {proc.stderr[-2000:]!r}"
        )
    return proc.stdout


def run_inference(
    guides: list[str],
    model: str,
    paths: DeepCRISPRPaths,
    s: Settings,
    *,
    run_fn: RunFn | None = None,
) -> dict:
    """Send `guides` to the legacy backend and return the parsed JSON payload.

    Raises `DeepCRISPRUnavailable` (backend missing/misconfigured) or
    `DeepCRISPRInferenceError` (nonzero exit, timeout, bad/short payload).
    """
    run = run_fn if run_fn is not None else _default_run_fn
    argv = build_command(paths, s)
    request = json.dumps({"guides": guides, "model": model})

    stdout = run(argv, request, s.deepcrispr_timeout_seconds)

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise DeepCRISPRInferenceError(f"DeepCRISPR subprocess returned non-JSON stdout: {stdout[:500]!r}") from e
    if isinstance(payload, dict) and payload.get("error"):
        raise DeepCRISPRInferenceError(f"Legacy DeepCRISPR inference reported an error: {payload['error']}")
    scores = payload.get("scores") if isinstance(payload, dict) else None
    if not isinstance(scores, list) or len(scores) != len(guides):
        raise DeepCRISPRInferenceError(
            f"Expected a 'scores' list of length {len(guides)}; got {scores!r}. "
            "The legacy protocol may have changed — verify the image/script version."
        )
    return payload
