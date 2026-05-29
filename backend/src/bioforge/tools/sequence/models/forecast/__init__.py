"""FORECasT (Allen et al. 2018) — per-guide edit-outcome predictor.

MIT-licensed, so NO consent gate. Python 3 + a compiled C++ component (indelmap), so it runs
OUT OF PROCESS in the authors' official image (quay.io/felicityallen/selftarget) or a local
install. This package is the modern-side glue and imports no FORECasT code.

Public API:
* `predict_forecast(sequence, pam_index)` -> `ForecastDistribution`.

Raises:
* `ForecastUnavailable`    — disabled, or the backend/runtime is absent/misconfigured.
* `ForecastInferenceError` — invalid input, or the subprocess failed.
"""

from bioforge.tools.sequence.models.forecast.inference import predict_forecast
from bioforge.tools.sequence.models.forecast.runner import (
    ForecastInferenceError,
    ForecastUnavailable,
)
from bioforge.tools.sequence.models.forecast.schema import ForecastDistribution

__all__ = [
    "ForecastDistribution",
    "ForecastInferenceError",
    "ForecastUnavailable",
    "predict_forecast",
]
