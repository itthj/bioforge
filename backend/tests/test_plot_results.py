"""Tests for plot_results — figure rendering across all 4 sub-modes.

No network involved; this exercises pure matplotlib/seaborn rendering plus
the input-validation contract (exactly one mode's data field populated).
"""

from __future__ import annotations

import base64

import pytest
from bioforge.tools import REGISTRY
from bioforge.tools.base import ToolError
from bioforge.tools.functional.plot_results import (
    GoDotPoint,
    LollipopPoint,
    PlotResultsInput,
    VolcanoPoint,
    plot_results,
)
from pydantic import ValidationError

# asyncio_mode = "auto" in pyproject.toml — async def tests run without an explicit marker.


# --- Registry --------------------------------------------------------------------


def test_plot_results_registered():
    assert "plot_results" in REGISTRY


def test_plot_results_metadata():
    spec = REGISTRY["plot_results"]
    assert spec.name == "plot_results"
    assert spec.description
    assert spec.version
    assert spec.citations
    assert "functional" in spec.tags
    assert "visualization" in spec.tags


# --- Shared helpers ----------------------------------------------------------------


def _assert_valid_png_b64(png_b64: str) -> None:
    raw = base64.b64decode(png_b64)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic bytes


def _assert_valid_svg(svg: str) -> None:
    assert "<svg" in svg
    assert "</svg>" in svg


# --- Mode-mixing / validation contract ----------------------------------------------


def test_volcano_mode_requires_volcano_data():
    with pytest.raises(ValidationError, match="volcano_data"):
        PlotResultsInput(mode="volcano")


def test_go_dotplot_mode_requires_go_data():
    with pytest.raises(ValidationError, match="go_data"):
        PlotResultsInput(mode="go_dotplot")


def test_heatmap_mode_requires_heatmap_matrix():
    with pytest.raises(ValidationError, match="heatmap_matrix"):
        PlotResultsInput(mode="heatmap")


def test_crispr_lollipop_mode_requires_lollipop_data():
    with pytest.raises(ValidationError, match="lollipop_data"):
        PlotResultsInput(mode="crispr_lollipop")


def test_mixing_two_modes_data_raises():
    with pytest.raises(ValidationError, match="also provided"):
        PlotResultsInput(
            mode="volcano",
            volcano_data=[VolcanoPoint(gene="TP53", log2_fold_change=2.0, p_adj=0.001)],
            go_data=[GoDotPoint(term_name="apoptosis", p_value_adjusted=0.01, n_genes_in_term=5, term_size=100)],
        )


def test_heatmap_ragged_columns_rejected():
    with pytest.raises(ValidationError, match="same column labels"):
        PlotResultsInput(
            mode="heatmap",
            heatmap_matrix={
                "geneA": {"s1": 1.0, "s2": 2.0},
                "geneB": {"s1": 1.0},  # missing s2
            },
        )


def test_empty_volcano_data_rejected():
    with pytest.raises(ValidationError):
        PlotResultsInput(mode="volcano", volcano_data=[])


# --- Volcano -------------------------------------------------------------------------


def _volcano_rows() -> list[VolcanoPoint]:
    return [
        VolcanoPoint(gene="TP53", log2_fold_change=3.2, p_adj=0.0001),
        VolcanoPoint(gene="BRCA1", log2_fold_change=-2.5, p_adj=0.0003),
        VolcanoPoint(gene="EGFR", log2_fold_change=0.2, p_adj=0.8),
        VolcanoPoint(gene="MYC", log2_fold_change=1.9, p_adj=0.04),
        VolcanoPoint(gene="ACTB", log2_fold_change=0.05, p_adj=0.95),
    ]


async def test_volcano_happy_path():
    out = await plot_results(PlotResultsInput(mode="volcano", volcano_data=_volcano_rows()))
    assert out.mode == "volcano"
    _assert_valid_png_b64(out.png_base64)
    _assert_valid_svg(out.svg)
    assert out.dpi == 150
    assert out.caveats


async def test_volcano_zero_pvalue_does_not_crash():
    """p_adj=0.0 must not raise from log10(0) — the tool clamps to an epsilon."""
    rows = [VolcanoPoint(gene="X", log2_fold_change=5.0, p_adj=0.0)]
    out = await plot_results(PlotResultsInput(mode="volcano", volcano_data=rows))
    _assert_valid_png_b64(out.png_base64)


async def test_volcano_custom_thresholds_and_title():
    out = await plot_results(
        PlotResultsInput(
            mode="volcano",
            volcano_data=_volcano_rows(),
            lfc_threshold=2.0,
            p_adj_threshold=0.01,
            title="My Volcano",
            label_top_n=2,
        )
    )
    assert out.mode == "volcano"
    assert any("meet the significance criteria" in c for c in out.caveats)


async def test_volcano_label_top_n_zero_still_renders():
    out = await plot_results(PlotResultsInput(mode="volcano", volcano_data=_volcano_rows(), label_top_n=0))
    _assert_valid_png_b64(out.png_base64)


def test_volcano_p_adj_out_of_range_rejected():
    with pytest.raises(ValidationError):
        VolcanoPoint(gene="X", log2_fold_change=1.0, p_adj=1.5)


# --- GO dotplot ------------------------------------------------------------------


def _go_rows(n: int = 5) -> list[GoDotPoint]:
    return [
        GoDotPoint(term_name=f"pathway_{i}", p_value_adjusted=0.001 * (i + 1), n_genes_in_term=i + 2, term_size=100 + i * 10)
        for i in range(n)
    ]


async def test_go_dotplot_happy_path():
    out = await plot_results(PlotResultsInput(mode="go_dotplot", go_data=_go_rows()))
    assert out.mode == "go_dotplot"
    _assert_valid_png_b64(out.png_base64)
    _assert_valid_svg(out.svg)


async def test_go_dotplot_truncates_to_max_terms():
    out = await plot_results(PlotResultsInput(mode="go_dotplot", go_data=_go_rows(30), max_terms=5))
    assert any("top 5 of 30" in c for c in out.caveats)


async def test_go_dotplot_single_term():
    out = await plot_results(PlotResultsInput(mode="go_dotplot", go_data=[GoDotPoint(term_name="solo", p_value_adjusted=0.05, n_genes_in_term=1, term_size=10)]))
    _assert_valid_png_b64(out.png_base64)


def test_go_dotplot_p_value_zero_rejected():
    """p_value_adjusted must be > 0 since it feeds a log10 color scale."""
    with pytest.raises(ValidationError):
        GoDotPoint(term_name="x", p_value_adjusted=0.0, n_genes_in_term=1, term_size=10)


# --- Heatmap -----------------------------------------------------------------------


def _heatmap_matrix(rows: int = 4, cols: int = 3) -> dict:
    return {
        f"gene{r}": {f"sample{c}": float(r * cols + c) for c in range(cols)}
        for r in range(rows)
    }


async def test_heatmap_happy_path_no_cluster():
    out = await plot_results(PlotResultsInput(mode="heatmap", heatmap_matrix=_heatmap_matrix()))
    assert out.mode == "heatmap"
    _assert_valid_png_b64(out.png_base64)
    _assert_valid_svg(out.svg)


async def test_heatmap_with_clustering():
    out = await plot_results(PlotResultsInput(mode="heatmap", heatmap_matrix=_heatmap_matrix(6, 5), cluster=True))
    assert any("clustered" in c for c in out.caveats)
    _assert_valid_png_b64(out.png_base64)


async def test_heatmap_clustering_ignored_for_single_row():
    """Clustering requires >=2 rows and >=2 cols; a 1-row matrix should render without crashing."""
    out = await plot_results(PlotResultsInput(mode="heatmap", heatmap_matrix=_heatmap_matrix(1, 3), cluster=True))
    assert any("ignored" in c for c in out.caveats)
    _assert_valid_png_b64(out.png_base64)


async def test_heatmap_custom_cmap():
    out = await plot_results(PlotResultsInput(mode="heatmap", heatmap_matrix=_heatmap_matrix(), cmap="magma"))
    _assert_valid_png_b64(out.png_base64)


# --- CRISPR lollipop -----------------------------------------------------------------


def _lollipop_rows() -> list[LollipopPoint]:
    return [
        LollipopPoint(position=10, value=0.5),
        LollipopPoint(position=25, value=0.9, label="hotspot"),
        LollipopPoint(position=40, value=0.1),
    ]


async def test_crispr_lollipop_happy_path():
    out = await plot_results(PlotResultsInput(mode="crispr_lollipop", lollipop_data=_lollipop_rows()))
    assert out.mode == "crispr_lollipop"
    _assert_valid_png_b64(out.png_base64)
    _assert_valid_svg(out.svg)
    assert any("3 positions" in c for c in out.caveats)


async def test_crispr_lollipop_with_reference_length():
    out = await plot_results(
        PlotResultsInput(mode="crispr_lollipop", lollipop_data=_lollipop_rows(), reference_length=100)
    )
    _assert_valid_png_b64(out.png_base64)


async def test_crispr_lollipop_unsorted_input_still_renders():
    rows = [LollipopPoint(position=50, value=1.0), LollipopPoint(position=5, value=2.0)]
    out = await plot_results(PlotResultsInput(mode="crispr_lollipop", lollipop_data=rows))
    _assert_valid_png_b64(out.png_base64)


# --- Output shape sanity ------------------------------------------------------------


async def test_output_has_positive_dimensions():
    out = await plot_results(PlotResultsInput(mode="volcano", volcano_data=_volcano_rows()))
    assert out.width_in > 0
    assert out.height_in > 0


async def test_tool_name_and_version_stamped_via_registry():
    from bioforge.tools.registry import execute_tool

    result = await execute_tool(
        "plot_results",
        {"mode": "volcano", "volcano_data": [{"gene": "X", "log2_fold_change": 1.0, "p_adj": 0.01}]},
    )
    assert result.tool_name == "plot_results"
    assert result.tool_version == "1.0.0"
