"""Out-of-process runner for Lindel inference.

Lindel is pure numpy/scipy with bundled weights, but we still run it in a pinned env
(uniform with the other ML scorers, and so a future weight/version bump is a clean env
rebuild). The env carries the Lindel install — no weight mount is needed — so the protocol
is just a JSON exchange on stdin/stdout:

    stdin :  {"sequences": ["<60bp>", ...]}
    stdout:  {"results": [{"frameshift_ratio": f, "predictions": {label: freq, ...}}, ...]}
             (or {"error": "..."})

Two backends via `settings.lindel_runner`: **docker** (a pinned image with Lindel + the
wrapper baked in) and **local** (`settings.lindel_python`, a venv/conda with Lindel
installed). `build_command` is pure + unit-testable; the launch goes through an injectable
`run_fn` so tests never spawn a subprocess.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from bioforge.config import Settings

# (argv, stdin_text, timeout_seconds) -> stdout_text
RunFn = Callable[[list[str], str, float], str]

_LEGACY_SCRIPT = Path(__file__).parent / "legacy" / "lindel_infer.py"
_CONTAINER_SCRIPT = "/opt/lindel/lindel_infer.py"


class LindelUnavailable(Exception):
    """Raised when the out-of-process Lindel runtime is not usable (disabled or misconfigured)."""


class LindelInferenceError(Exception):
    """Raised when the Lindel subprocess fails or returns an unexpected payload."""


def build_command(s: Settings) -> list[str]:
    """Construct the subprocess argv for the configured backend. Pure + testable.

    Raises `LindelUnavailable` when the backend is misconfigured (missing image or python
    path, unknown runner) so the tool boundary surfaces actionable setup guidance.
    """
    runner = (s.lindel_runner or "docker").lower()
    if runner == "docker":
        if not s.lindel_docker_image:
            raise LindelUnavailable(
                "lindel_runner='docker' but BIOFORGE_LINDEL_DOCKER_IMAGE is unset. Build the env "
                "(see models/lindel/legacy/Dockerfile) and set the var to its digest-pinned reference."
            )
        return ["docker", "run", "--rm", "-i", s.lindel_docker_image, "python", _CONTAINER_SCRIPT]
    if runner == "local":
        if not s.lindel_python:
            raise LindelUnavailable(
                "lindel_runner='local' but BIOFORGE_LINDEL_PYTHON is unset. Create the env "
                "(see models/lindel/legacy/environment.yml, then `python setup.py install` Lindel) "
                "and set the var to that interpreter."
            )
        return [s.lindel_python, str(_LEGACY_SCRIPT)]
    raise LindelUnavailable(f"Unknown lindel_runner {runner!r}; expected 'docker' or 'local'.")


def _default_run_fn(argv: list[str], stdin_text: str, timeout: float) -> str:
    import subprocess

    try:
        proc = subprocess.run(  # noqa: S603 — argv built from validated settings, not user text
            argv, input=stdin_text, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as e:
        raise LindelUnavailable(
            f"Runner executable {argv[0]!r} not found. Is Docker (or the configured python) installed? {e}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise LindelInferenceError(f"Lindel subprocess timed out after {timeout}s.") from e
    if proc.returncode != 0:
        raise LindelInferenceError(f"Lindel subprocess exited {proc.returncode}. stderr tail: {proc.stderr[-2000:]!r}")
    return proc.stdout


def run_inference(sequences: list[str], s: Settings, *, run_fn: RunFn | None = None) -> dict:
    """Send `sequences` to the Lindel backend and return the parsed JSON payload.

    Raises `LindelUnavailable` (backend missing/misconfigured) or `LindelInferenceError`
    (nonzero exit, timeout, or a results list whose length does not match the request).
    """
    run = run_fn if run_fn is not None else _default_run_fn
    argv = build_command(s)
    stdout = run(argv, json.dumps({"sequences": sequences}), s.lindel_timeout_seconds)

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise LindelInferenceError(f"Lindel subprocess returned non-JSON stdout: {stdout[:500]!r}") from e
    if isinstance(payload, dict) and payload.get("error"):
        raise LindelInferenceError(f"Legacy Lindel inference reported an error: {payload['error']}")
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list) or len(results) != len(sequences):
        raise LindelInferenceError(
            f"Expected a 'results' list of length {len(sequences)}; got {results!r}. "
            "The legacy protocol may have changed — verify the image/script version."
        )
    return payload
