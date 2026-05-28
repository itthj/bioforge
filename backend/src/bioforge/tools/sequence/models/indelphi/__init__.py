"""inDelphi (Shen 2018) — Cas9 edit-outcome predictor.

Public API:

* `ensure_available(cell_type, settings=...)` — fetch + verify upstream
  sources, returning local paths.
* `predict(sequence, cutsite, cell_type=...)` → `InDelphiDistribution` —
  run inference and return typed results.

Both can raise:
* `InDelphiConsentRequired` — consent flag unset; user must opt in per
  LICENSE_NOTICE.md.
* `InDelphiUnavailable` — `[indelphi]` optional deps not installed.
* `InDelphiInferenceError` — upstream rejected input or returned an
  unexpected shape.
* `InDelphiFetchError` — download or hash-verify failure.

Read [LICENSE_NOTICE.md](./LICENSE_NOTICE.md) BEFORE enabling. inDelphi is
non-commercial-research-only and BioForge does not bundle its weights.
"""

from bioforge.tools.sequence.models.indelphi.fetcher import (
    InDelphiConsentRequired,
    InDelphiFetchError,
    InDelphiUnavailable,
    ensure_available,
)
from bioforge.tools.sequence.models.indelphi.inference import (
    InDelphiInferenceError,
    predict,
)
from bioforge.tools.sequence.models.indelphi.schema import (
    InDelphiDistribution,
    InDelphiOutcome,
    InDelphiStats,
)

__all__ = [
    "InDelphiConsentRequired",
    "InDelphiDistribution",
    "InDelphiFetchError",
    "InDelphiInferenceError",
    "InDelphiOutcome",
    "InDelphiStats",
    "InDelphiUnavailable",
    "ensure_available",
    "predict",
]
