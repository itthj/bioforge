"""inDelphi inference wrapper.

Responsibilities:

1. Lazy-load the fetched upstream `inDelphi.py` (dynamic import from the data
   dir, since it lives outside our package tree).
2. Call upstream `init_model(celltype=...)` once per process per cell type.
3. Invoke upstream `predict(seq, cutsite)`, detect the string-return error
   path, and raise a typed exception.
4. Map the returned `(DataFrame, stats_dict)` into our Pydantic
   `InDelphiDistribution` schema.

Three subtleties worth knowing:

* **sklearn version assertion.** Upstream `init_model` hard-asserts on
  `sklearn.__version__ in ('0.18.1', '0.20.0')`. Modern sklearn reads the
  0.20-era pickles fine; only the assertion is the blocker. We temporarily
  spoof the version string across the call. Documented warts ahead.
* **Process-global model state.** Upstream uses module-level globals for the
  loaded model, so a process can only have ONE cell type active. Switching
  cell types triggers a re-import of the upstream module under a fresh name
  (cached separately).
* **Optional deps at call time.** `pandas` and `sklearn` are NOT in core
  deps — they're under the `[indelphi]` extra. `predict()` raises
  `InDelphiUnavailable` with install instructions if they're missing.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from types import ModuleType
from typing import Any

from bioforge.config import Settings
from bioforge.tools.sequence.models.indelphi.fetcher import (
    InDelphiPaths,
    InDelphiUnavailable,
    ensure_available,
)
from bioforge.tools.sequence.models.indelphi.manifest import CellType
from bioforge.tools.sequence.models.indelphi.schema import (
    InDelphiDistribution,
    InDelphiOutcome,
    InDelphiStats,
)


class InDelphiInferenceError(Exception):
    """Raised when upstream `predict()` returns an error string, or when the
    DataFrame returned by upstream is missing expected columns."""


EnsureFn = Callable[..., InDelphiPaths]

# Cache of dynamically-loaded upstream modules, keyed by cell type. Each entry
# is a fully-initialized upstream module (init_model already called). Lookups
# are O(1); we never reload an entry once cached.
_LOADED: dict[CellType, ModuleType] = {}


def _check_optional_deps() -> None:
    """Raise InDelphiUnavailable if pandas or sklearn aren't importable."""
    missing: list[str] = []
    for pkg in ("pandas", "sklearn"):
        if importlib.util.find_spec(pkg) is None:
            missing.append("scikit-learn" if pkg == "sklearn" else pkg)
    if missing:
        raise InDelphiUnavailable(
            f"inDelphi requires optional dependencies that are not installed: {missing}. "
            "Install them with `pip install -e .[indelphi]` (after reading "
            "backend/src/bioforge/tools/sequence/models/indelphi/LICENSE_NOTICE.md)."
        )


def _load_upstream(paths: InDelphiPaths, celltype: CellType) -> ModuleType:
    """Dynamically import the fetched inDelphi.py and run its init_model().

    The upstream script is NOT in our package, so we use importlib's
    spec-from-file-location to load it from `paths.script`. We assign a unique
    module name (`_indelphi_upstream_<celltype>`) so different cell types get
    distinct module instances — upstream uses module globals for the loaded
    model and would otherwise overwrite each other.

    sklearn version spoof: upstream init_model() asserts on hard-coded version
    strings. We override `sklearn.__version__` for the duration of init only.
    The pickles themselves deserialize correctly on modern sklearn — only the
    assert is the blocker.
    """
    module_name = f"_indelphi_upstream_{celltype}"
    spec = importlib.util.spec_from_file_location(module_name, paths.script)
    if spec is None or spec.loader is None:
        raise InDelphiInferenceError(
            f"Could not build importlib spec for {paths.script}. The fetched file may be missing or corrupt."
        )
    module = importlib.util.module_from_spec(spec)
    # Register before exec so upstream's `import` statements that reference
    # itself don't trigger a second load.
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        sys.modules.pop(module_name, None)
        raise InDelphiInferenceError(
            f"Failed to load upstream inDelphi.py from {paths.script}: {e}. "
            "If the upstream commit pin was updated, delete the data dir and re-fetch."
        ) from e

    import sklearn  # noqa: F401 — imported only so we can spoof the version

    real_version = sklearn.__version__
    try:
        # Upstream supports exactly two sklearn versions via assert; spoof to
        # the one matching the model dir we ship (model-sklearn-0.20.0).
        sklearn.__version__ = "0.20.0"
        module.init_model(celltype=celltype)
    except Exception as e:
        sys.modules.pop(module_name, None)
        raise InDelphiInferenceError(
            f"Upstream init_model failed for celltype={celltype!r}: {e}. "
            "Most likely cause: the fetched pickle files are corrupt or the sklearn version "
            "on this machine cannot load 0.20-era pickles. Try `pip install 'scikit-learn>=1.5,<2'`."
        ) from e
    finally:
        sklearn.__version__ = real_version
    return module


def _get_or_load(
    celltype: CellType,
    *,
    settings: Settings | None = None,
    ensure_fn: EnsureFn = ensure_available,
) -> ModuleType:
    """Return the upstream module for `celltype`, loading + initializing on
    first request. Subsequent calls return the cached module without I/O."""
    cached = _LOADED.get(celltype)
    if cached is not None:
        return cached
    paths = ensure_fn(celltype=celltype, settings=settings)
    module = _load_upstream(paths, celltype)
    _LOADED[celltype] = module
    return module


# --- Result conversion --------------------------------------------------------------


_STATS_KEY_MAP: dict[str, str] = {
    "Phi": "phi",
    "Precision": "precision",
    "1-bp ins frequency": "one_bp_ins_frequency",
    "MH del frequency": "mh_del_frequency",
    "MHless del frequency": "mhless_del_frequency",
    "Frameshift frequency": "frameshift_frequency",
    "Frame +0 frequency": "frame_plus_0_frequency",
    "Frame +1 frequency": "frame_plus_1_frequency",
    "Frame +2 frequency": "frame_plus_2_frequency",
    "Highest outcome frequency": "highest_outcome_frequency",
    "Highest del frequency": "highest_del_frequency",
    "Highest ins frequency": "highest_ins_frequency",
    "Expected indel length": "expected_indel_length",
}

_REQUIRED_DF_COLS: tuple[str, ...] = ("Category", "Length", "Predicted frequency")


def _coerce_genotype_position(raw: Any) -> int | None:
    """Upstream sometimes puts the literal string `'e'` in `Genotype position`
    to mark elsewhere-aggregated deletion buckets. Anything else should be int-coercible."""
    if raw is None:
        return None
    if isinstance(raw, str):
        return None if raw == "e" else int(raw)
    # Numpy/pandas numeric — relies on `__int__`.
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _map_result(
    pred_df: Any,  # pandas.DataFrame; typed loosely to avoid the optional-import dep here
    stats_dict: dict[str, Any],
    *,
    cell_type: CellType,
    cutsite: int,
    sequence_length: int,
) -> InDelphiDistribution:
    """Map upstream (DataFrame, dict) into our InDelphiDistribution schema.

    Sorts outcomes by predicted frequency descending so the most-likely
    repair events surface first in any UI rendering.
    """
    missing_cols = [c for c in _REQUIRED_DF_COLS if c not in pred_df.columns]
    if missing_cols:
        raise InDelphiInferenceError(
            f"Upstream DataFrame missing expected columns {missing_cols}. "
            f"Got columns: {list(pred_df.columns)}. The upstream API may have changed; "
            "verify the commit pin matches the schema this wrapper was written against."
        )

    outcomes: list[InDelphiOutcome] = []
    for row in pred_df.itertuples(index=False):
        # itertuples preserves column names as field names (pandas munges spaces to underscores).
        row_dict = row._asdict()
        category_raw = row_dict["Category"]
        category = "deletion" if category_raw == "del" else "insertion" if category_raw == "ins" else None
        if category is None:
            # Upstream might add new categories in a future release; skip unknowns rather than crash.
            continue
        outcomes.append(
            InDelphiOutcome(
                category=category,
                length=int(row_dict["Length"]),
                genotype_position=_coerce_genotype_position(
                    row_dict.get("Genotype_position") or row_dict.get("Genotype position")
                ),
                inserted_bases=row_dict.get("Inserted_Bases") or row_dict.get("Inserted Bases") or None,
                predicted_frequency=float(row_dict["Predicted frequency"]),
            )
        )
    outcomes.sort(key=lambda o: o.predicted_frequency, reverse=True)

    stats_kwargs: dict[str, float] = {}
    for upstream_key, our_key in _STATS_KEY_MAP.items():
        if upstream_key in stats_dict:
            stats_kwargs[our_key] = float(stats_dict[upstream_key])

    return InDelphiDistribution(
        cell_type=cell_type,
        cutsite=cutsite,
        sequence_length=sequence_length,
        outcomes=outcomes,
        stats=InDelphiStats(**stats_kwargs),
    )


# --- Public predict ------------------------------------------------------------------


def predict(
    sequence: str,
    cutsite: int,
    *,
    cell_type: CellType = "mESC",
    settings: Settings | None = None,
    ensure_fn: EnsureFn = ensure_available,
    _upstream_override: ModuleType | None = None,
) -> InDelphiDistribution:
    """Predict the indel-outcome distribution for one Cas9 cut.

    Parameters
    ----------
    sequence : ACGT-only DNA, length ≥ 2.
    cutsite  : 0-based cut position; must satisfy `1 <= cutsite <= len(sequence) - 1`
               (upstream constraint — anything outside raises InDelphiInferenceError).
    cell_type : One of `manifest.SUPPORTED_CELLTYPES`.
    settings  : Override the default Settings (used for tests / multi-tenant).
    ensure_fn : Override `ensure_available` (used for tests).

    Returns
    -------
    InDelphiDistribution with `outcomes` sorted by `predicted_frequency` desc.

    Raises
    ------
    InDelphiConsentRequired : consent flag unset (propagated from ensure_fn).
    InDelphiUnavailable     : optional deps missing.
    InDelphiInferenceError  : upstream returned an error string, or DataFrame
                              shape didn't match the expected schema.
    """
    if _upstream_override is None:
        _check_optional_deps()
        module = _get_or_load(cell_type, settings=settings, ensure_fn=ensure_fn)
    else:
        module = _upstream_override

    result = module.predict(sequence, cutsite)
    # Upstream returns a string on error, a tuple on success.
    if isinstance(result, str):
        raise InDelphiInferenceError(
            f"Upstream inDelphi.predict rejected the input: {result}. "
            f"Constraints: sequence must be ACGT only; cutsite must satisfy "
            f"1 <= cutsite <= len(sequence) - 1."
        )
    if not isinstance(result, tuple) or len(result) != 2:
        raise InDelphiInferenceError(
            f"Upstream predict returned unexpected shape: {type(result).__name__}. Expected (DataFrame, dict)."
        )
    pred_df, stats_dict = result
    return _map_result(
        pred_df,
        stats_dict,
        cell_type=cell_type,
        cutsite=cutsite,
        sequence_length=len(sequence),
    )


def reset_cache_for_tests() -> None:
    """Clear the module cache. Tests use this for isolation; production code shouldn't."""
    _LOADED.clear()
