"""Tests for the inDelphi inference wrapper.

These tests run WITHOUT pandas or sklearn installed. The wrapper accepts a
`_upstream_override` argument; tests pass a fake module + a fake DataFrame
stub that exposes the small surface area `_map_result` uses (`columns` and
`itertuples(index=False)._asdict()`).

A separate, opt-in fidelity test (`test_indelphi_fidelity.py`, follow-up
slice) exercises the real model — it skips if the user hasn't enabled the
non-commercial consent flag.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from bioforge.tools.sequence.models.indelphi import (
    InDelphiDistribution,
    InDelphiInferenceError,
    InDelphiUnavailable,
    predict,
)
from bioforge.tools.sequence.models.indelphi.inference import (
    _map_result,
    reset_cache_for_tests,
)

# --- Fake pandas DataFrame -----------------------------------------------------------


class FakeRow:
    """Mimics pandas' itertuples() namedtuple — `_asdict()` is all _map_result uses."""

    def __init__(self, **fields: Any) -> None:
        self._fields_dict = fields

    def _asdict(self) -> dict[str, Any]:
        return dict(self._fields_dict)


class FakeDataFrame:
    """Minimal stand-in for pandas.DataFrame. Only `.columns` and `.itertuples(index=False)`
    are touched by the inference wrapper, so we don't need to ship pandas to test it."""

    def __init__(self, rows: list[dict[str, Any]], columns: list[str]) -> None:
        self._rows = rows
        self.columns = columns

    def itertuples(self, index: bool = False):
        for r in self._rows:
            yield FakeRow(**r)


def _make_fake_upstream(
    df: FakeDataFrame | None = None,
    stats: dict[str, Any] | None = None,
    *,
    predict_returns: Any = None,
) -> SimpleNamespace:
    """Build a module-like object with a predict() method.

    If `predict_returns` is set, predict returns that verbatim (lets a test
    force the error-string branch). Otherwise predict returns (df, stats).
    """

    def _predict(seq: str, cutsite: int) -> Any:
        if predict_returns is not None:
            return predict_returns
        return (df, stats or {})

    return SimpleNamespace(predict=_predict)


# --- _map_result direct tests --------------------------------------------------------


def test_map_result_converts_deletion_and_insertion_rows() -> None:
    df = FakeDataFrame(
        rows=[
            {"Category": "del", "Length": 3, "Genotype position": 2, "Predicted frequency": 12.5},
            {"Category": "ins", "Length": 1, "Genotype position": 0, "Inserted Bases": "A", "Predicted frequency": 8.0},
            {"Category": "del", "Length": 5, "Genotype position": "e", "Predicted frequency": 4.2},
        ],
        columns=["Category", "Length", "Genotype position", "Inserted Bases", "Predicted frequency"],
    )
    stats = {
        "Phi": 0.42,
        "Precision": 0.66,
        "Frameshift frequency": 75.0,
        "1-bp ins frequency": 8.0,
        "MH del frequency": 12.5,
        "MHless del frequency": 4.2,
        "Frame +0 frequency": 25.0,
        "Frame +1 frequency": 40.0,
        "Frame +2 frequency": 35.0,
    }

    result = _map_result(df, stats, cell_type="mESC", cutsite=27, sequence_length=60)

    assert isinstance(result, InDelphiDistribution)
    assert result.cell_type == "mESC"
    assert result.cutsite == 27
    assert result.sequence_length == 60

    # Outcomes sorted by frequency desc.
    freqs = [o.predicted_frequency for o in result.outcomes]
    assert freqs == sorted(freqs, reverse=True)

    deletion_3 = next(o for o in result.outcomes if o.category == "deletion" and o.length == 3)
    assert deletion_3.genotype_position == 2
    assert deletion_3.inserted_bases is None
    assert deletion_3.predicted_frequency == pytest.approx(12.5)

    insertion = next(o for o in result.outcomes if o.category == "insertion")
    assert insertion.inserted_bases == "A"

    # 'e' bucket → None
    e_bucket = next(o for o in result.outcomes if o.category == "deletion" and o.length == 5)
    assert e_bucket.genotype_position is None

    # Stats key mapping correct.
    assert result.stats.phi == pytest.approx(0.42)
    assert result.stats.frameshift_frequency == pytest.approx(75.0)
    assert result.stats.one_bp_ins_frequency == pytest.approx(8.0)
    assert result.stats.frame_plus_1_frequency == pytest.approx(40.0)


def test_map_result_tolerates_underscore_column_variants() -> None:
    """itertuples may munge `Genotype position` → `Genotype_position`; the
    wrapper should accept either form."""
    df = FakeDataFrame(
        rows=[
            {"Category": "del", "Length": 2, "Genotype_position": 5, "Predicted frequency": 10.0},
            {"Category": "ins", "Length": 1, "Genotype_position": 0, "Inserted_Bases": "T", "Predicted frequency": 5.0},
        ],
        columns=["Category", "Length", "Genotype position", "Inserted Bases", "Predicted frequency"],
    )
    result = _map_result(df, {}, cell_type="mESC", cutsite=10, sequence_length=30)

    deletion = next(o for o in result.outcomes if o.category == "deletion")
    insertion = next(o for o in result.outcomes if o.category == "insertion")
    assert deletion.genotype_position == 5
    assert insertion.inserted_bases == "T"


def test_map_result_skips_unknown_categories() -> None:
    df = FakeDataFrame(
        rows=[
            {"Category": "del", "Length": 1, "Genotype position": 0, "Predicted frequency": 50.0},
            {"Category": "FUTURE_CATEGORY", "Length": 7, "Genotype position": 1, "Predicted frequency": 25.0},
            {
                "Category": "ins",
                "Length": 1,
                "Genotype position": 0,
                "Inserted Bases": "G",
                "Predicted frequency": 10.0,
            },
        ],
        columns=["Category", "Length", "Genotype position", "Inserted Bases", "Predicted frequency"],
    )
    result = _map_result(df, {}, cell_type="mESC", cutsite=10, sequence_length=30)
    # Two valid categories; future one dropped silently.
    assert len(result.outcomes) == 2
    assert {o.category for o in result.outcomes} == {"deletion", "insertion"}


def test_map_result_raises_on_missing_required_columns() -> None:
    df = FakeDataFrame(
        rows=[{"Category": "del"}],
        columns=["Category"],  # missing Length, Predicted frequency
    )
    with pytest.raises(InDelphiInferenceError) as exc:
        _map_result(df, {}, cell_type="mESC", cutsite=10, sequence_length=30)
    assert "missing expected columns" in str(exc.value).lower()
    assert "Length" in str(exc.value)
    assert "Predicted frequency" in str(exc.value)


def test_map_result_ignores_unknown_stats_keys() -> None:
    """Future upstream stats keys we don't yet map should be ignored, not crash."""
    df = FakeDataFrame(rows=[], columns=["Category", "Length", "Predicted frequency"])
    stats = {"Phi": 1.0, "SomeNewMetric": 999.0}
    result = _map_result(df, stats, cell_type="mESC", cutsite=5, sequence_length=20)
    assert result.stats.phi == 1.0


# --- predict() with injected upstream ------------------------------------------------


def test_predict_returns_typed_distribution() -> None:
    reset_cache_for_tests()
    df = FakeDataFrame(
        rows=[
            {"Category": "del", "Length": 1, "Genotype position": 0, "Predicted frequency": 30.0},
            {"Category": "del", "Length": 2, "Genotype position": 1, "Predicted frequency": 15.0},
            {
                "Category": "ins",
                "Length": 1,
                "Genotype position": 0,
                "Inserted Bases": "A",
                "Predicted frequency": 20.0,
            },
        ],
        columns=["Category", "Length", "Genotype position", "Inserted Bases", "Predicted frequency"],
    )
    stats = {"Phi": 0.5, "Frameshift frequency": 65.0}
    fake = _make_fake_upstream(df, stats)

    result = predict("ACGTACGTACGTACGTACGT", cutsite=10, cell_type="mESC", _upstream_override=fake)

    assert isinstance(result, InDelphiDistribution)
    assert result.cutsite == 10
    assert result.sequence_length == 20
    # Sorted desc → del-1 first.
    assert result.outcomes[0].length == 1
    assert result.outcomes[0].category == "deletion"
    assert result.stats.frameshift_frequency == pytest.approx(65.0)


def test_predict_raises_on_upstream_error_string() -> None:
    """Upstream signals errors by returning a string instead of a tuple. The
    wrapper must convert that to a typed exception with the upstream message."""
    reset_cache_for_tests()
    fake = _make_fake_upstream(predict_returns="Cutsite index is not within the sequence.")
    with pytest.raises(InDelphiInferenceError) as exc:
        predict("ACGT", cutsite=99, _upstream_override=fake)
    assert "Cutsite index is not within the sequence" in str(exc.value)


def test_predict_raises_on_unexpected_upstream_shape() -> None:
    reset_cache_for_tests()
    # Upstream returns a single int — neither (df, dict) nor str.
    fake = _make_fake_upstream(predict_returns=42)
    with pytest.raises(InDelphiInferenceError) as exc:
        predict("ACGTACGT", cutsite=3, _upstream_override=fake)
    assert "unexpected shape" in str(exc.value).lower()


def test_predict_without_optional_deps_raises_unavailable() -> None:
    """When no override is supplied and pandas/sklearn aren't installed in
    the test venv, predict() must surface the optional-deps install hint."""
    reset_cache_for_tests()
    # No _upstream_override → real path. pandas/sklearn aren't installed in
    # the test venv, so this should raise InDelphiUnavailable BEFORE any
    # network or filesystem activity.
    with pytest.raises(InDelphiUnavailable) as exc:
        predict("ACGTACGT", cutsite=3)
    msg = str(exc.value)
    assert "pip install" in msg
    assert "[indelphi]" in msg


# --- Cache behavior ------------------------------------------------------------------


def test_cache_isolation_via_reset() -> None:
    """`reset_cache_for_tests()` clears the module cache so tests don't see
    each other's state. Without it, an injected override from one test could
    leak into a subsequent test that expects the real (uninitialized) cache.
    """
    from bioforge.tools.sequence.models.indelphi.inference import _LOADED

    reset_cache_for_tests()
    assert _LOADED == {}

    # Populate the cache by hand and confirm reset clears it.
    _LOADED["mESC"] = SimpleNamespace()  # type: ignore[assignment]
    assert "mESC" in _LOADED
    reset_cache_for_tests()
    assert _LOADED == {}
