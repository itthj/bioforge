"""DeepCRISPR on-target inference orchestration (the py3.11 side).

Public entry point: `predict_on_target(guides)`. It validates the inputs, runs the
out-of-process legacy backend via `runner`, and maps the JSON result into the typed
`DeepCRISPROnTargetResult` schema.

No TensorFlow is imported here — that lives entirely in the legacy subprocess. The legacy
environment (the thin image FROM the authors' image, or a local DeepCRISPR install) carries
the trained weights, so there is no separate weight fetch from this side. This module stays
import-safe in the modern interpreter.
"""

from __future__ import annotations

from bioforge.config import Settings
from bioforge.config import settings as _default_settings
from bioforge.tools.sequence.models.deepcrispr.fetcher import DeepCRISPRUnavailable
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
    run_fn: RunFn | None = None,
) -> DeepCRISPROnTargetResult:
    """Score one or more 23 bp guides with the DeepCRISPR seq-only on-target model.

    Parameters
    ----------
    guides : list of 23 bp ACGT windows (protospacer + PAM), 5'->3'.
    model  : the on-target model id (only the seq-only regression model is wired up).
    settings / run_fn : injection points for tests.

    Raises
    ------
    DeepCRISPRUnavailable    : `deepcrispr_enabled` is False, or the legacy backend/runtime is
                               missing or misconfigured.
    DeepCRISPRInferenceError : invalid input, or the subprocess failed / returned an
                               unexpected payload.
    """
    s = settings if settings is not None else _default_settings

    if not s.deepcrispr_enabled:
        raise DeepCRISPRUnavailable(
            "DeepCRISPR is disabled. After building the legacy image (models/deepcrispr/legacy/, "
            "FROM michaelchuai/deepcrispr:latest), set BIOFORGE_DEEPCRISPR_ENABLED=true and "
            "BIOFORGE_DEEPCRISPR_DOCKER_IMAGE (docker) or BIOFORGE_DEEPCRISPR_PYTHON (local). Until "
            "then, use the deterministic rule-based on-target score."
        )
    if not guides:
        raise DeepCRISPRInferenceError("No guides provided to DeepCRISPR.")

    cleaned = [_validate_guide(g) for g in guides]
    payload = run_inference(cleaned, model, s, run_fn=run_fn)

    raw_scores = payload["scores"]
    scores = [DeepCRISPROnTargetScore(guide=g, score=float(v)) for g, v in zip(cleaned, raw_scores, strict=True)]
    return DeepCRISPROnTargetResult(
        model=model,
        model_version=f"{model}@{s.deepcrispr_upstream_commit}",
        scores=scores,
    )
