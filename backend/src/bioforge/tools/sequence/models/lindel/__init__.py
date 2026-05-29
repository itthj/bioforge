"""Lindel (Chen et al. 2019) — logistic-regression per-guide edit-outcome predictor.

MIT-licensed, so NO consent gate. Runs OUT OF PROCESS in a pinned env (the env carries the
bundled weights; no separate fetch). This package is the modern-side glue and imports no
Lindel code.

Public API:
* `predict_lindel(window)` -> `LindelDistribution` — score a 60 bp edit window.

Raises:
* `LindelUnavailable`    — disabled, or the backend/runtime is absent/misconfigured.
* `LindelInferenceError` — invalid window, or the subprocess failed.
"""

from bioforge.tools.sequence.models.lindel.inference import LINDEL_WINDOW_BP, predict_lindel
from bioforge.tools.sequence.models.lindel.runner import (
    LindelInferenceError,
    LindelUnavailable,
)
from bioforge.tools.sequence.models.lindel.schema import LindelDistribution

__all__ = [
    "LINDEL_WINDOW_BP",
    "LindelDistribution",
    "LindelInferenceError",
    "LindelUnavailable",
    "predict_lindel",
]
