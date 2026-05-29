"""FORECasT edit-outcome inference orchestration (the modern side).

`predict_forecast(sequence, pam_index)` validates the basic contract (ACGT target, a PAM
index that fits), runs the out-of-process FORECasT backend, and maps the JSON result into
the typed `ForecastDistribution`. No FORECasT code is imported here — it lives entirely in
the subprocess, so this module stays import-safe in the modern interpreter.

FORECasT consumes a target sequence + the 0-based PAM index *on the protospacer strand*;
`edit_outcome` supplies that index directly (it is the PAM position `_locate_guide` already
found), and FORECasT itself enforces the surrounding window, so a wrong index fails loudly
in the subprocess rather than silently scoring the wrong site.
"""

from __future__ import annotations

from bioforge.config import Settings
from bioforge.config import settings as _default_settings
from bioforge.tools.sequence.models.forecast.runner import (
    ForecastInferenceError,
    ForecastUnavailable,
    RunFn,
    run_inference,
)
from bioforge.tools.sequence.models.forecast.schema import ForecastDistribution

_PAM_LEN = 3
_DNA = set("ACGT")
# A protospacer (20) + PAM (3) is the bare minimum; FORECasT wants flanking context too and
# enforces its own window, but we reject obviously-too-short inputs up front.
_MIN_SEQUENCE_BP = 23


def _validate_sequence(sequence: str) -> str:
    cleaned = "".join(sequence.split()).upper()
    if len(cleaned) < _MIN_SEQUENCE_BP:
        raise ForecastInferenceError(
            f"FORECasT needs a target of at least {_MIN_SEQUENCE_BP} bp (protospacer + PAM + flank); "
            f"got {len(cleaned)} bp."
        )
    bad = set(cleaned) - _DNA
    if bad:
        raise ForecastInferenceError(f"FORECasT target must be ACGT only; found {sorted(bad)!r}.")
    return cleaned


def predict_forecast(
    sequence: str,
    pam_index: int,
    *,
    settings: Settings | None = None,
    run_fn: RunFn | None = None,
) -> ForecastDistribution:
    """Predict the FORECasT indel-outcome distribution for one gRNA.

    `sequence` is the target on the protospacer strand; `pam_index` is the 0-based start of
    the PAM within it. Raises `ForecastUnavailable` (disabled or backend missing) or
    `ForecastInferenceError` (invalid input, or the subprocess failed / returned an
    unexpected payload).
    """
    s = settings if settings is not None else _default_settings
    if not s.forecast_enabled:
        raise ForecastUnavailable(
            "FORECasT is disabled. After making the env available (the official image "
            "quay.io/felicityallen/selftarget, or a local install — see models/forecast/legacy/), "
            "set BIOFORGE_FORECAST_ENABLED=true and configure a runner. Until then, use "
            "edit_outcome's rule_of_thumb model."
        )
    cleaned = _validate_sequence(sequence)
    if not (0 <= pam_index <= len(cleaned) - _PAM_LEN):
        raise ForecastInferenceError(
            f"pam_index {pam_index} is out of range for a {len(cleaned)} bp target (need "
            f"0 <= pam_index <= {len(cleaned) - _PAM_LEN})."
        )
    payload = run_inference([{"sequence": cleaned, "pam_index": int(pam_index)}], s, run_fn=run_fn)
    result = payload["results"][0]
    predictions = {str(k): float(v) for k, v in (result.get("predictions") or {}).items()}
    return ForecastDistribution(sequence_length=len(cleaned), predictions=predictions)
