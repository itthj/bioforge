"""Azimuth / Doench Rule Set 2 on-target inference orchestration (the py3.11 side).

Public entry point: `predict_on_target(thirtymers)`. It validates the inputs, runs the
out-of-process legacy backend via `runner`, and maps the JSON result into the typed
`AzimuthOnTargetResult` schema. No scikit-learn / Azimuth is imported here -- that lives
entirely in the legacy subprocess, so this module stays import-safe in the modern interpreter.
"""

from __future__ import annotations

from bioforge.config import Settings
from bioforge.config import settings as _default_settings
from bioforge.tools.sequence.models.azimuth.manifest import (
    DEFAULT_MODEL,
    THIRTYMER_LENGTH,
    AzimuthModel,
)
from bioforge.tools.sequence.models.azimuth.runner import (
    AzimuthInferenceError,
    AzimuthUnavailable,
    RunFn,
    run_inference,
)
from bioforge.tools.sequence.models.azimuth.schema import (
    AzimuthOnTargetResult,
    AzimuthOnTargetScore,
)

_DNA = set("ACGT")


def _validate_thirtymer(seq: str) -> str:
    """Require an exact 30 nt ACGT window (4 nt 5' + 20 nt protospacer + 3 nt PAM + 3 nt 3')."""
    cleaned = "".join(seq.split()).upper()
    if len(cleaned) != THIRTYMER_LENGTH:
        raise AzimuthInferenceError(
            f"Azimuth / Rule Set 2 input must be exactly {THIRTYMER_LENGTH} nt "
            f"(4 nt 5' context + 20 nt protospacer + 3 nt PAM + 3 nt 3' context); got {len(cleaned)} nt for {seq!r}."
        )
    bad = set(cleaned) - _DNA
    if bad:
        raise AzimuthInferenceError(
            f"Azimuth input must be ACGT only; {seq!r} contains {sorted(bad)!r}. "
            "Ambiguous bases are not supported by the model encoding."
        )
    return cleaned


def predict_on_target(
    thirtymers: list[str],
    *,
    model: AzimuthModel = DEFAULT_MODEL,
    settings: Settings | None = None,
    run_fn: RunFn | None = None,
) -> AzimuthOnTargetResult:
    """Score one or more 30 nt windows with the Azimuth / Doench Rule Set 2 model.

    Parameters
    ----------
    thirtymers : list of 30 nt ACGT windows (4 nt 5' + 20 nt protospacer + 3 nt PAM + 3 nt 3'), 5'->3'.
    model      : the Azimuth model id; the sequence-only `V3_model_nopos` is the default.
    settings / run_fn : injection points for tests.

    Raises
    ------
    AzimuthUnavailable    : `azimuth_enabled` is False, or the legacy backend/runtime is
                            missing or misconfigured.
    AzimuthInferenceError : invalid input, or the subprocess failed / returned an unexpected payload.
    """
    s = settings if settings is not None else _default_settings

    if not s.azimuth_enabled:
        raise AzimuthUnavailable(
            "Azimuth / Rule Set 2 is disabled. After building the legacy image "
            "(models/azimuth/legacy/), set BIOFORGE_AZIMUTH_ENABLED=true and "
            "BIOFORGE_AZIMUTH_DOCKER_IMAGE (docker) or BIOFORGE_AZIMUTH_PYTHON (local). Until "
            "then, use the deterministic rule-based on-target score."
        )
    if not thirtymers:
        raise AzimuthInferenceError("No 30-mers provided to Azimuth.")

    cleaned = [_validate_thirtymer(t) for t in thirtymers]
    payload = run_inference(cleaned, model, s, run_fn=run_fn)

    raw_scores = payload["scores"]
    scores = [AzimuthOnTargetScore(thirtymer=t, score=float(v)) for t, v in zip(cleaned, raw_scores, strict=True)]
    return AzimuthOnTargetResult(model=model, model_version=f"{model}@{s.azimuth_upstream_commit}", scores=scores)
