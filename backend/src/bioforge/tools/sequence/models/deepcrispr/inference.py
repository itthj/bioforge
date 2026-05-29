"""DeepCRISPR on-target inference orchestration (the py3.11 side).

Public entry point: `predict_on_target(guides)`. It validates the inputs, ensures
the Apache-2.0 weights are present (fetch-on-first-use), runs the out-of-process
legacy backend via `runner`, and maps the JSON result into the typed
`DeepCRISPROnTargetResult` schema.

No TensorFlow is imported here — that lives entirely in the legacy subprocess. This
module stays import-safe in the modern interpreter, so the package can be imported
during normal operation (and tests) without the legacy environment present.
"""

from __future__ import annotations

from collections.abc import Callable

from bioforge.config import Settings
from bioforge.config import settings as _default_settings
from bioforge.tools.sequence.models.deepcrispr.fetcher import (
    DeepCRISPRPaths,
    DeepCRISPRUnavailable,
    ensure_available,
)
from bioforge.tools.sequence.models.deepcrispr.manifest import (
    GUIDE_LENGTH_BP,
    OnTargetModel,
)
from bioforge.tools.sequence.models.deepcrispr.runner import (
    DeepCRISPRInferenceError,
    RunFn,
    run_inference,
)
from bioforge.tools.sequence.models.deepcrispr.schema import (
    DeepCRISPROnTargetResult,
    DeepCRISPROnTargetScore,
)

EnsureFn = Callable[..., DeepCRISPRPaths]

_DNA = set("ACGT")


def _validate_guide(guide: str) -> str:
    """Require an exact 23 bp ACGT window (20 nt protospacer + 3 nt PAM)."""
    cleaned = "".join(guide.split()).upper()
    if len(cleaned) != GUIDE_LENGTH_BP:
        raise DeepCRISPRInferenceError(
            f"DeepCRISPR on-target input must be exactly {GUIDE_LENGTH_BP} bp "
            f"(20 nt protospacer + 3 nt PAM); got {len(cleaned)} bp for {guide!r}."
        )
    bad = set(cleaned) - _DNA
    if bad:
        raise DeepCRISPRInferenceError(
            f"DeepCRISPR on-target input must be ACGT only; {guide!r} contains {sorted(bad)!r}. "
            "Ambiguous bases are not supported by the model encoding."
        )
    return cleaned


def predict_on_target(
    guides: list[str],
    *,
    model: OnTargetModel = "ontar_cnn_reg_seq",
    settings: Settings | None = None,
    ensure_fn: EnsureFn = ensure_available,
    run_fn: RunFn | None = None,
) -> DeepCRISPROnTargetResult:
    """Score one or more 23 bp guides with the DeepCRISPR seq-only on-target model.

    Parameters
    ----------
    guides : list of 23 bp ACGT windows (protospacer + PAM), 5'->3'.
    model  : the on-target model id (only the seq-only regression model is wired up).
    settings / ensure_fn / run_fn : injection points for tests.

    Raises
    ------
    DeepCRISPRUnavailable    : `deepcrispr_enabled` is False, or the legacy
                               backend/runtime is missing or misconfigured.
    DeepCRISPRInferenceError : invalid input, or the subprocess failed / returned
                               an unexpected payload.
    """
    s = settings if settings is not None else _default_settings

    if not s.deepcrispr_enabled:
        raise DeepCRISPRUnavailable(
            "DeepCRISPR is disabled. After building the legacy environment "
            "(models/deepcrispr/legacy/), set BIOFORGE_DEEPCRISPR_ENABLED=true and configure a "
            "runner: either BIOFORGE_DEEPCRISPR_DOCKER_IMAGE (docker) or BIOFORGE_DEEPCRISPR_PYTHON "
            "(local conda). Until then, use the deterministic rule-based on-target score."
        )
    if not guides:
        raise DeepCRISPRInferenceError("No guides provided to DeepCRISPR.")

    cleaned = [_validate_guide(g) for g in guides]

    paths = ensure_fn(model, settings=s)
    payload = run_inference(cleaned, model, paths, s, run_fn=run_fn)

    raw_scores = payload["scores"]
    scores = [DeepCRISPROnTargetScore(guide=g, score=float(v)) for g, v in zip(cleaned, raw_scores, strict=True)]
    return DeepCRISPROnTargetResult(
        model=model,
        model_version=f"{model}@{s.deepcrispr_upstream_commit}",
        scores=scores,
    )
