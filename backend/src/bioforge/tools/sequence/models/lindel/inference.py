"""Lindel on-target edit-outcome inference orchestration (the modern side).

`predict_lindel(window)` validates the 60 bp / ACGT contract, runs the out-of-process
Lindel backend, and maps the JSON result into the typed `LindelDistribution`. No Lindel
code or weights are imported here — that lives entirely in the subprocess, so this module
stays import-safe in the modern interpreter.

The modern side validates only length + alphabet; Lindel itself enforces the exact PAM/cut
framing (PAM `NGG` near position 33, cut between 30 bp of flank each side), so a misframed
window fails LOUDLY in the subprocess rather than silently scoring the wrong sequence.
"""

from __future__ import annotations

from bioforge.config import Settings
from bioforge.config import settings as _default_settings
from bioforge.tools.sequence.models.lindel.runner import (
    LindelInferenceError,
    LindelUnavailable,
    RunFn,
    run_inference,
)
from bioforge.tools.sequence.models.lindel.schema import LindelDistribution

LINDEL_WINDOW_BP = 60
_DNA = set("ACGT")


def _validate_window(sequence: str) -> str:
    cleaned = "".join(sequence.split()).upper()
    if len(cleaned) != LINDEL_WINDOW_BP:
        raise LindelInferenceError(
            f"Lindel requires a {LINDEL_WINDOW_BP} bp window (30 bp upstream + 30 bp downstream of "
            f"the cut, PAM near position 33); got {len(cleaned)} bp."
        )
    bad = set(cleaned) - _DNA
    if bad:
        raise LindelInferenceError(f"Lindel window must be ACGT only; found {sorted(bad)!r}.")
    return cleaned


def predict_lindel(
    sequence: str,
    *,
    settings: Settings | None = None,
    run_fn: RunFn | None = None,
) -> LindelDistribution:
    """Predict the Lindel indel-outcome distribution for one 60 bp edit window.

    Raises `LindelUnavailable` (disabled or backend missing/misconfigured) or
    `LindelInferenceError` (invalid window, or the subprocess failed / returned an
    unexpected payload).
    """
    s = settings if settings is not None else _default_settings
    if not s.lindel_enabled:
        raise LindelUnavailable(
            "Lindel is disabled. After building the env (models/lindel/legacy/), set "
            "BIOFORGE_LINDEL_ENABLED=true and configure a runner (docker image or local python). "
            "Until then, use edit_outcome's rule_of_thumb model."
        )
    window = _validate_window(sequence)
    payload = run_inference([window], s, run_fn=run_fn)
    result = payload["results"][0]
    predictions = {str(k): float(v) for k, v in (result.get("predictions") or {}).items()}
    return LindelDistribution(
        sequence_length=len(window),
        frameshift_ratio=float(result.get("frameshift_ratio", 0.0)),
        predictions=predictions,
    )
