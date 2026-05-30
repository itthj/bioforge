"""Azimuth / Doench Rule Set 2 (Doench 2016) -- on-target sgRNA efficiency scorer.

Rule Set 2 is a gradient-boosted regression over ~500 sequence features -- a trained model
shipped as scikit-learn pickles in the upstream repo -- integrated the same out-of-process way
as DeepCRISPR. BSD-3-Clause (verified 2026-05-30, docs/license_audit.md), so there is NO consent
gate. The trained pickles are fragile across scikit-learn versions, so Azimuth executes OUT OF
PROCESS in a pinned legacy environment. This package is the modern-side glue; it imports no
scikit-learn / Azimuth.

Public API:

* `predict_on_target(thirtymers)` -> `AzimuthOnTargetResult` -- score 30-nt windows
  (4 nt 5' context + 20 nt protospacer + 3 nt PAM + 3 nt 3' context) with Rule Set 2.

Raises:
* `AzimuthUnavailable`    -- disabled, or the legacy backend/runtime is absent.
* `AzimuthInferenceError` -- invalid input, or the subprocess failed.
"""

from bioforge.tools.sequence.models.azimuth.inference import predict_on_target
from bioforge.tools.sequence.models.azimuth.manifest import (
    DEFAULT_MODEL,
    SUPPORTED_MODELS,
    THIRTYMER_LENGTH,
    AzimuthModel,
)
from bioforge.tools.sequence.models.azimuth.runner import (
    AzimuthInferenceError,
    AzimuthUnavailable,
)
from bioforge.tools.sequence.models.azimuth.schema import (
    AzimuthOnTargetResult,
    AzimuthOnTargetScore,
)

__all__ = [
    "DEFAULT_MODEL",
    "SUPPORTED_MODELS",
    "THIRTYMER_LENGTH",
    "AzimuthInferenceError",
    "AzimuthModel",
    "AzimuthOnTargetResult",
    "AzimuthOnTargetScore",
    "AzimuthUnavailable",
    "predict_on_target",
]
