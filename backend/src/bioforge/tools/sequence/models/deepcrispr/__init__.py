"""DeepCRISPR (Chuai 2018) — deep on-target sgRNA efficacy scorer.

Apache-2.0, so there is NO consent gate (unlike inDelphi). DeepCRISPR runs
TensorFlow 1.3 / Python 3.6, which cannot import into this interpreter, so it
executes OUT OF PROCESS in a pinned legacy environment (Docker image or conda
python). This package is the modern-side glue; it imports no TensorFlow.

Public API:

* `predict_on_target(guides)` -> `DeepCRISPROnTargetResult` — score 23 bp windows
  (20 nt protospacer + 3 nt PAM) with the sequence-only CNN regression model.
* `ensure_available()` — fetch + extract the Apache-2.0 weights on first use.

Raises:
* `DeepCRISPRUnavailable`    — disabled, or the legacy backend/runtime is absent.
* `DeepCRISPRFetchError`     — weight download / extract / hash-verify failure.
* `DeepCRISPRInferenceError` — invalid input, or the subprocess failed.
"""

from bioforge.tools.sequence.models.deepcrispr.fetcher import (
    DeepCRISPRFetchError,
    DeepCRISPRPaths,
    DeepCRISPRUnavailable,
    ensure_available,
)
from bioforge.tools.sequence.models.deepcrispr.inference import predict_on_target
from bioforge.tools.sequence.models.deepcrispr.manifest import (
    GUIDE_LENGTH_BP,
    SUPPORTED_ONTARGET_MODELS,
    OnTargetModel,
)
from bioforge.tools.sequence.models.deepcrispr.runner import DeepCRISPRInferenceError
from bioforge.tools.sequence.models.deepcrispr.schema import (
    DeepCRISPROnTargetResult,
    DeepCRISPROnTargetScore,
)

__all__ = [
    "GUIDE_LENGTH_BP",
    "SUPPORTED_ONTARGET_MODELS",
    "DeepCRISPRFetchError",
    "DeepCRISPRInferenceError",
    "DeepCRISPROnTargetResult",
    "DeepCRISPROnTargetScore",
    "DeepCRISPRPaths",
    "DeepCRISPRUnavailable",
    "OnTargetModel",
    "ensure_available",
    "predict_on_target",
]
