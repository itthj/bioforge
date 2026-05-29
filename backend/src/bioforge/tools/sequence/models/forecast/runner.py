"""Out-of-process runner for FORECasT inference.

FORECasT is Python 3 + a compiled C++ component (indelmap), so it runs in the authors'
official image (`quay.io/felicityallen/selftarget`) by default, or a local environment that
has FORECasT installed. We talk to it over a JSON protocol on stdin/stdout:

    stdin :  {"requests": [{"sequence": "<target>", "pam_index": <int>}, ...]}
    stdout:  {"results": [{"predictions": {label: freq, ...}}, ...]}   (or {"error": "..."})

The legacy wrapper (`legacy/forecast_infer.py`) runs INSIDE that env. For the **docker**
backend the wrapper is bind-mounted into the container (the official image does not contain
it); for **local** it is invoked directly. `build_command` is pure + unit-testable and the
launch goes through an injectable `run_fn`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from bioforge.config import Settings

# (argv, stdin_text, timeout_seconds) -> stdout_text
RunFn = Callable[[list[str], str, float], str]

_LEGACY_DIR = Path(__file__).parent / "legacy"
_LEGACY_SCRIPT = _LEGACY_DIR / "forecast_infer.py"
_CONTAINER_DIR = "/opt/forecast"


class ForecastUnavailable(Exception):
    """Raised when the out-of-process FORECasT runtime is not usable (disabled or misconfigured)."""


class ForecastInferenceError(Exception):
    """Raised when the FORECasT subprocess fails or returns an unexpected payload."""


def build_command(s: Settings) -> list[str]:
    """Construct the subprocess argv for the configured backend. Pure + testable.

    For docker we bind-mount the wrapper dir into the official image (which does not ship
    it). Raises `ForecastUnavailable` when the backend is misconfigured.
    """
    runner = (s.forecast_runner or "docker").lower()
    if runner == "docker":
        if not s.forecast_docker_image:
            raise ForecastUnavailable(
                "forecast_runner='docker' but BIOFORGE_FORECAST_DOCKER_IMAGE is unset. Use the "
                "authors' image (quay.io/felicityallen/selftarget) or a thin image built from it "
                "(see models/forecast/legacy/Dockerfile)."
            )
        return [
            "docker",
            "run",
            "--rm",
            "-i",
            "-v",
            f"{_LEGACY_DIR}:{_CONTAINER_DIR}:ro",
            s.forecast_docker_image,
            "python",
            f"{_CONTAINER_DIR}/forecast_infer.py",
        ]
    if runner == "local":
        if not s.forecast_python:
            raise ForecastUnavailable(
                "forecast_runner='local' but BIOFORGE_FORECAST_PYTHON is unset. Install FORECasT "
                "(SelfTarget) and set the var to that interpreter."
            )
        return [s.forecast_python, str(_LEGACY_SCRIPT)]
    raise ForecastUnavailable(f"Unknown forecast_runner {runner!r}; expected 'docker' or 'local'.")


def _default_run_fn(argv: list[str], stdin_text: str, timeout: float) -> str:
    import subprocess

    try:
        proc = subprocess.run(  # noqa: S603 — argv built from validated settings, not user text
            argv, input=stdin_text, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as e:
        raise ForecastUnavailable(
            f"Runner executable {argv[0]!r} not found. Is Docker (or the configured python) installed? {e}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise ForecastInferenceError(f"FORECasT subprocess timed out after {timeout}s.") from e
    if proc.returncode != 0:
        raise ForecastInferenceError(
            f"FORECasT subprocess exited {proc.returncode}. stderr tail: {proc.stderr[-2000:]!r}"
        )
    return proc.stdout


def run_inference(requests: list[dict], s: Settings, *, run_fn: RunFn | None = None) -> dict:
    """Send `requests` (each {"sequence", "pam_index"}) to FORECasT; return the JSON payload.

    Raises `ForecastUnavailable` (backend missing/misconfigured) or `ForecastInferenceError`
    (nonzero exit, timeout, or a results list whose length does not match the request).
    """
    run = run_fn if run_fn is not None else _default_run_fn
    argv = build_command(s)
    stdout = run(argv, json.dumps({"requests": requests}), s.forecast_timeout_seconds)

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise ForecastInferenceError(f"FORECasT subprocess returned non-JSON stdout: {stdout[:500]!r}") from e
    if isinstance(payload, dict) and payload.get("error"):
        raise ForecastInferenceError(f"Legacy FORECasT inference reported an error: {payload['error']}")
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list) or len(results) != len(requests):
        raise ForecastInferenceError(
            f"Expected a 'results' list of length {len(requests)}; got {results!r}. "
            "The legacy protocol may have changed — verify the image/script version."
        )
    return payload
