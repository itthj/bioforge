"""Out-of-process runner for Azimuth / Doench Rule Set 2 inference.

Azimuth's trained scikit-learn pickles (`saved_models/V3_model_*.pickle`) are fragile across
scikit-learn versions, so -- like the other ML scorers -- Azimuth runs in a pinned env and we
talk to it over a JSON protocol on stdin/stdout:

    stdin :  {"thirtymers": ["<30nt>", ...], "model": "V3_model_nopos"}
    stdout:  {"model": "...", "scores": [<float>, ...]}   (or {"error": "..."})

Two backends via `settings.azimuth_runner`: **docker** (a pinned image with the Azimuth install
+ committed pickles + the wrapper baked in) and **local** (`settings.azimuth_python`, a venv/
conda with Azimuth installed). `build_command` is pure + unit-testable; the launch goes through
an injectable `run_fn` so tests never spawn a subprocess.

SCAFFOLD: the legacy image is not yet built/validated end-to-end (see models/azimuth/legacy).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from bioforge.config import Settings

# (argv, stdin_text, timeout_seconds) -> stdout_text
RunFn = Callable[[list[str], str, float], str]

_LEGACY_SCRIPT = Path(__file__).parent / "legacy" / "azimuth_infer.py"
_CONTAINER_SCRIPT = "/opt/azimuth/azimuth_infer.py"


class AzimuthUnavailable(Exception):
    """Raised when the out-of-process Azimuth runtime is not usable (disabled or misconfigured)."""


class AzimuthInferenceError(Exception):
    """Raised when the Azimuth subprocess fails or returns an unexpected payload."""


def build_command(s: Settings) -> list[str]:
    """Construct the subprocess argv for the configured backend. Pure + testable.

    Raises `AzimuthUnavailable` when the backend is misconfigured (missing image or python
    path, unknown runner) so the tool boundary surfaces actionable setup guidance.
    """
    runner = (s.azimuth_runner or "docker").lower()
    if runner == "docker":
        if not s.azimuth_docker_image:
            raise AzimuthUnavailable(
                "azimuth_runner='docker' but BIOFORGE_AZIMUTH_DOCKER_IMAGE is unset. Build the env "
                "(see models/azimuth/legacy/Dockerfile) and set the var to its digest-pinned reference."
            )
        return ["docker", "run", "--rm", "-i", s.azimuth_docker_image, "python", _CONTAINER_SCRIPT]
    if runner == "local":
        if not s.azimuth_python:
            raise AzimuthUnavailable(
                "azimuth_runner='local' but BIOFORGE_AZIMUTH_PYTHON is unset. Create the env "
                "(see models/azimuth/legacy/README.md) and set the var to that interpreter."
            )
        return [s.azimuth_python, str(_LEGACY_SCRIPT)]
    raise AzimuthUnavailable(f"Unknown azimuth_runner {runner!r}; expected 'docker' or 'local'.")


def _default_run_fn(argv: list[str], stdin_text: str, timeout: float) -> str:
    import subprocess

    try:
        proc = subprocess.run(  # noqa: S603 -- argv built from validated settings, not user text
            argv, input=stdin_text, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as e:
        raise AzimuthUnavailable(
            f"Runner executable {argv[0]!r} not found. Is Docker (or the configured python) installed? {e}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise AzimuthInferenceError(f"Azimuth subprocess timed out after {timeout}s.") from e
    if proc.returncode != 0:
        raise AzimuthInferenceError(
            f"Azimuth subprocess exited {proc.returncode}. stderr tail: {proc.stderr[-2000:]!r}"
        )
    return proc.stdout


def run_inference(thirtymers: list[str], model: str, s: Settings, *, run_fn: RunFn | None = None) -> dict:
    """Send `thirtymers` to the Azimuth backend and return the parsed JSON payload.

    Raises `AzimuthUnavailable` (backend missing/misconfigured) or `AzimuthInferenceError`
    (nonzero exit, timeout, or a scores list whose length does not match the request).
    """
    run = run_fn if run_fn is not None else _default_run_fn
    argv = build_command(s)
    stdout = run(argv, json.dumps({"thirtymers": thirtymers, "model": model}), s.azimuth_timeout_seconds)

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise AzimuthInferenceError(f"Azimuth subprocess returned non-JSON stdout: {stdout[:500]!r}") from e
    if isinstance(payload, dict) and payload.get("error"):
        raise AzimuthInferenceError(f"Legacy Azimuth inference reported an error: {payload['error']}")
    scores = payload.get("scores") if isinstance(payload, dict) else None
    if not isinstance(scores, list) or len(scores) != len(thirtymers):
        raise AzimuthInferenceError(
            f"Expected a 'scores' list of length {len(thirtymers)}; got {scores!r}. "
            "The legacy protocol may have changed -- verify the image/script version."
        )
    return payload
