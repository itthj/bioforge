"""Generate publication-quality figures from BioForge analysis results.

Every major journal expects a standard set of figure types for the analyses
BioForge already runs: a volcano plot for `differential_expression`, a dot
plot for `go_enrichment`, a heatmap for any gene x sample matrix, and a
lollipop/needle plot for CRISPR editing outcomes along a sequence. Rather
than have the agent hand-roll matplotlib code per request, this tool takes
the *already-typed* result rows from those tools and renders a figure
directly — so results can go straight from analysis to manuscript figure
inside the same conversation.

Four sub-modes, one shared `PlotResultsInput`:
  - `volcano`         — log2FC vs -log10(padj) scatter, thresholds shaded,
                         top hits labelled. Feed it `differential_expression`
                         rows directly (`gene`, `log2_fold_change`, `p_adj`).
  - `go_dotplot`       — enrichment dot plot: gene ratio on x, term on y,
                         dot size = genes-in-term, color = -log10(padj).
                         Feed it `go_enrichment` rows directly (`term_name`,
                         `p_value_adjusted`, `n_genes_in_term`, `term_size`).
  - `heatmap`          — row/col-labelled matrix, optional hierarchical
                         clustering (seaborn clustermap) on both axes.
  - `crispr_lollipop`  — stem/needle plot of a per-position value (editing
                         frequency, off-target score, etc.) along a sequence
                         or amplicon coordinate.

Each mode validates that ONLY its own data field is populated — mixing modes
raises `ToolError` with guidance rather than silently picking one.

Output: both a base64-encoded PNG (150 DPI, matplotlib default for on-screen/
inline use) and a raw SVG string (vector, for journal submission — most
journals require vector figures). Rendering runs in a thread pool since
matplotlib is synchronous.
"""

from __future__ import annotations

import asyncio
import base64
import io
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool


class PlotMode(str, Enum):
    volcano = "volcano"
    go_dotplot = "go_dotplot"
    heatmap = "heatmap"
    crispr_lollipop = "crispr_lollipop"


# --- Per-mode row schemas, deliberately field-compatible with the tools that produce them ---


class VolcanoPoint(BaseModel):
    gene: str
    log2_fold_change: float
    p_adj: float = Field(ge=0.0, le=1.0, description="Adjusted p-value. 0 is clamped to a small epsilon for plotting.")


class GoDotPoint(BaseModel):
    term_name: str
    p_value_adjusted: float = Field(gt=0.0, le=1.0, description="Must be > 0 (used for -log10 color scale).")
    n_genes_in_term: int = Field(ge=0, description="Query genes annotated to this term — sets dot size.")
    term_size: int = Field(ge=1, description="Total genes annotated to this term genome-wide — denominator of gene ratio.")


class LollipopPoint(BaseModel):
    position: int = Field(description="1-based coordinate along the reference/amplicon.")
    value: float = Field(description="The plotted quantity at this position (frequency, score, count, ...).")
    label: str | None = Field(default=None, description="Optional short annotation shown above the stem.")


class PlotResultsInput(ToolInput):
    mode: PlotMode = Field(description="Which figure type to render.")
    title: str | None = Field(default=None, max_length=200, description="Figure title. Mode-appropriate default used when omitted.")

    # volcano
    volcano_data: list[VolcanoPoint] | None = Field(
        default=None,
        description="Required when mode='volcano'. Pass differential_expression's `results` rows directly.",
    )
    lfc_threshold: float = Field(default=1.0, ge=0.0, description="|log2FC| cutoff shading significance (volcano only).")
    p_adj_threshold: float = Field(default=0.05, gt=0.0, le=1.0, description="Adjusted p-value cutoff (volcano only).")
    label_top_n: int = Field(default=10, ge=0, le=50, description="Label the N most significant genes by padj (volcano only).")

    # go_dotplot
    go_data: list[GoDotPoint] | None = Field(
        default=None,
        description="Required when mode='go_dotplot'. Pass go_enrichment's `terms` rows directly.",
    )
    max_terms: int = Field(default=20, ge=1, le=100, description="Plot only the top-N terms by adjusted p-value (go_dotplot only).")

    # heatmap
    heatmap_matrix: dict[str, dict[str, float]] | None = Field(
        default=None,
        description=(
            "Required when mode='heatmap'. Nested dict {row_label: {col_label: value}}, e.g. "
            "{gene: {sample: expression}}. All rows must share the same set of column labels."
        ),
    )
    cluster: bool = Field(default=False, description="Hierarchically cluster rows and columns (heatmap only).")
    cmap: str = Field(default="vlag", max_length=32, description="Matplotlib/seaborn colormap name (heatmap only).")

    # crispr_lollipop
    lollipop_data: list[LollipopPoint] | None = Field(
        default=None,
        description="Required when mode='crispr_lollipop'. One point per position of interest.",
    )
    reference_length: int | None = Field(
        default=None,
        ge=1,
        description="Total reference/amplicon length in bp, used to draw the x-axis baseline (crispr_lollipop only).",
    )

    @model_validator(mode="after")
    def _validate_mode_data(self) -> "PlotResultsInput":
        provided = {
            "volcano": self.volcano_data is not None,
            "go_dotplot": self.go_data is not None,
            "heatmap": self.heatmap_matrix is not None,
            "crispr_lollipop": self.lollipop_data is not None,
        }
        active = [name for name, present in provided.items() if present]
        wanted = self.mode.value
        if not provided[wanted]:
            raise ValueError(
                f"mode={wanted!r} requires its matching data field "
                f"({'volcano_data' if wanted == 'volcano' else 'go_data' if wanted == 'go_dotplot' else 'heatmap_matrix' if wanted == 'heatmap' else 'lollipop_data'}), "
                "which was not provided."
            )
        extra = [name for name in active if name != wanted]
        if extra:
            raise ValueError(
                f"mode={wanted!r} but data was also provided for {extra}. "
                "Provide data for exactly one mode per call — make one plot_results call per figure."
            )
        if wanted == "volcano" and len(self.volcano_data) < 1:
            raise ValueError("volcano_data must contain at least one row.")
        if wanted == "go_dotplot" and len(self.go_data) < 1:
            raise ValueError("go_data must contain at least one row.")
        if wanted == "heatmap":
            if not self.heatmap_matrix:
                raise ValueError("heatmap_matrix must contain at least one row.")
            col_sets = {frozenset(cols) for cols in self.heatmap_matrix.values()}
            if len(col_sets) > 1:
                raise ValueError(
                    "heatmap_matrix rows do not share the same column labels. "
                    "Every row must have a value for every column."
                )
        if wanted == "crispr_lollipop" and len(self.lollipop_data) < 1:
            raise ValueError("lollipop_data must contain at least one row.")
        return self


class PlotResultsOutput(ToolOutput):
    mode: str
    png_base64: str = Field(description="Base64-encoded PNG, 150 DPI, ready for <img src=\"data:image/png;base64,...\">.")
    svg: str = Field(description="Raw SVG markup — vector, suitable for journal submission or further editing.")
    width_in: float
    height_in: float
    dpi: int = 150
    caveats: list[str] = Field(default_factory=list)


# --- Rendering (synchronous matplotlib; run in a thread) ---------------------------


def _fig_to_outputs(fig) -> tuple[str, str]:
    png_buf = io.BytesIO()
    fig.savefig(png_buf, format="png", dpi=150, bbox_inches="tight")
    png_b64 = base64.b64encode(png_buf.getvalue()).decode("ascii")

    svg_buf = io.StringIO()
    fig.savefig(svg_buf, format="svg", bbox_inches="tight")
    svg = svg_buf.getvalue()
    return png_b64, svg


def _render_volcano(inp: PlotResultsInput):
    import math

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = inp.volcano_data
    EPS = 1e-300
    xs = [r.log2_fold_change for r in rows]
    ys = [-math.log10(max(r.p_adj, EPS)) for r in rows]
    sig = [
        (r.p_adj < inp.p_adj_threshold) and (abs(r.log2_fold_change) >= inp.lfc_threshold)
        for r in rows
    ]
    colors = []
    for r, s in zip(rows, sig):
        if not s:
            colors.append("#999999")
        elif r.log2_fold_change > 0:
            colors.append("#d62728")
        else:
            colors.append("#1f77b4")

    fig, ax = plt.subplots(figsize=(7, 6))
    ax.scatter(xs, ys, c=colors, s=18, alpha=0.75, linewidths=0)
    ax.axvline(inp.lfc_threshold, color="grey", linestyle="--", linewidth=0.8)
    ax.axvline(-inp.lfc_threshold, color="grey", linestyle="--", linewidth=0.8)
    ax.axhline(-math.log10(inp.p_adj_threshold), color="grey", linestyle="--", linewidth=0.8)
    ax.set_xlabel("log2(fold change)")
    ax.set_ylabel("-log10(adjusted p-value)")
    ax.set_title(inp.title or "Volcano plot")

    # Label the top-N most significant genes (lowest padj) among significant points.
    labelled = sorted(
        (r for r, s in zip(rows, sig) if s),
        key=lambda r: r.p_adj,
    )[: inp.label_top_n]
    for r in labelled:
        y = -math.log10(max(r.p_adj, EPS))
        ax.annotate(
            r.gene,
            (r.log2_fold_change, y),
            fontsize=7,
            xytext=(3, 3),
            textcoords="offset points",
        )

    n_sig = sum(sig)
    fig.tight_layout()
    return fig, [
        f"{n_sig} of {len(rows)} points meet the significance criteria shown "
        f"(padj < {inp.p_adj_threshold}, |log2FC| >= {inp.lfc_threshold}).",
        "Point color encodes significance + direction only; it is not a statistical test in itself.",
    ]


def _render_go_dotplot(inp: PlotResultsInput):
    import math

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = sorted(inp.go_data, key=lambda r: r.p_value_adjusted)[: inp.max_terms]
    rows = list(reversed(rows))  # so the most significant term plots at the top

    terms = [r.term_name for r in rows]
    ratios = [r.n_genes_in_term / r.term_size for r in rows]
    sizes = [max(r.n_genes_in_term, 1) * 20 for r in rows]
    neg_log_p = [-math.log10(r.p_value_adjusted) for r in rows]

    fig_height = max(3.0, 0.35 * len(rows) + 1.5)
    fig, ax = plt.subplots(figsize=(8, fig_height))
    sc = ax.scatter(ratios, range(len(rows)), s=sizes, c=neg_log_p, cmap="viridis", edgecolors="black", linewidths=0.3)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(terms, fontsize=8)
    ax.set_xlabel("Gene ratio (genes in term / term size)")
    ax.set_title(inp.title or "GO / pathway enrichment")
    cbar = fig.colorbar(sc, ax=ax)
    cbar.set_label("-log10(adjusted p-value)")
    fig.tight_layout()
    return fig, [
        f"Showing top {len(rows)} of {len(inp.go_data)} supplied terms, ranked by adjusted p-value.",
        "Dot size encodes the number of query genes annotated to each term, not statistical strength.",
    ]


def _render_heatmap(inp: PlotResultsInput):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import seaborn as sns

    df = pd.DataFrame(inp.heatmap_matrix).T  # rows x cols
    df = df[sorted(df.columns)]

    n_rows, n_cols = df.shape
    width = max(4.0, 0.5 * n_cols + 2.0)
    height = max(3.0, 0.3 * n_rows + 2.0)

    if inp.cluster and n_rows > 1 and n_cols > 1:
        grid = sns.clustermap(df, cmap=inp.cmap, figsize=(width, height))
        fig = grid.figure
        fig.suptitle(inp.title or "Heatmap", y=1.02)
        notes = ["Rows and columns hierarchically clustered (seaborn clustermap, default Euclidean/average linkage)."]
    else:
        fig, ax = plt.subplots(figsize=(width, height))
        sns.heatmap(df, cmap=inp.cmap, ax=ax, linewidths=0.3, linecolor="white")
        ax.set_title(inp.title or "Heatmap")
        fig.tight_layout()
        notes = [] if not inp.cluster else ["cluster=True was requested but ignored: clustering needs at least 2 rows and 2 columns."]
    return fig, notes


def _render_crispr_lollipop(inp: PlotResultsInput):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = sorted(inp.lollipop_data, key=lambda r: r.position)
    positions = [r.position for r in rows]
    values = [r.value for r in rows]

    fig, ax = plt.subplots(figsize=(max(6.0, 0.05 * len(positions) + 4.0), 4.5))
    markerline, stemlines, baseline = ax.stem(positions, values, basefmt=" ")
    plt.setp(stemlines, linewidth=1.2, color="#4c72b0")
    plt.setp(markerline, markersize=5, color="#c44e52")

    for r in rows:
        if r.label:
            ax.annotate(r.label, (r.position, r.value), fontsize=7, xytext=(0, 5), textcoords="offset points", ha="center")

    if inp.reference_length:
        ax.set_xlim(0, inp.reference_length)
    ax.axhline(0, color="black", linewidth=0.6)
    ax.set_xlabel("Position (bp)")
    ax.set_ylabel("Value")
    ax.set_title(inp.title or "CRISPR editing outcome by position")
    fig.tight_layout()
    return fig, [f"{len(rows)} positions plotted along the reference."]


_RENDERERS = {
    PlotMode.volcano: _render_volcano,
    PlotMode.go_dotplot: _render_go_dotplot,
    PlotMode.heatmap: _render_heatmap,
    PlotMode.crispr_lollipop: _render_crispr_lollipop,
}


def _render_sync(inp: PlotResultsInput) -> tuple[str, str, float, float, list[str]]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    renderer = _RENDERERS[inp.mode]
    fig, notes = renderer(inp)
    try:
        width_in, height_in = fig.get_size_inches()
        png_b64, svg = _fig_to_outputs(fig)
    finally:
        plt.close(fig)
    return png_b64, svg, float(width_in), float(height_in), notes


@register_tool(
    name="plot_results",
    description=(
        "Render a publication-quality figure from BioForge analysis results: a volcano plot "
        "(from differential_expression rows), a GO/pathway enrichment dot plot (from "
        "go_enrichment rows), a labelled heatmap (from any row x column numeric matrix, with "
        "optional hierarchical clustering), or a CRISPR editing-outcome lollipop plot (per-"
        "position values along a reference). Use when the user asks to 'plot', 'visualize', "
        "'make a figure', or 'chart' results from one of those analyses, or wants a figure "
        "suitable for a manuscript. Returns both a base64 PNG (150 DPI, for inline display) "
        "and raw SVG (vector, for journal submission). Exactly one mode's data field must be "
        "populated per call — make separate calls for separate figures."
    ),
    input_model=PlotResultsInput,
    output_model=PlotResultsOutput,
    version="1.0.0",
    citations=[
        "Hunter JD (2007) Matplotlib: A 2D graphics environment. Comput Sci Eng 9(3):90-95.",
        "Waskom ML (2021) seaborn: statistical data visualization. J Open Source Softw 6(60):3021.",
    ],
    cost_hint="cheap",
    tags=["functional", "visualization", "plotting", "figures"],
)
async def plot_results(inp: PlotResultsInput) -> PlotResultsOutput:
    try:
        loop = asyncio.get_event_loop()
        png_b64, svg, width_in, height_in, notes = await loop.run_in_executor(None, _render_sync, inp)
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(f"Figure rendering failed ({inp.mode.value}): {type(e).__name__}: {e}") from e

    caveats = list(notes) + [
        "This figure is a direct rendering of the data supplied to plot_results — it performs "
        "no additional statistical analysis. Verify the underlying values against their source "
        "tool's output.",
    ]

    return PlotResultsOutput(
        mode=inp.mode.value,
        png_base64=png_b64,
        svg=svg,
        width_in=round(width_in, 2),
        height_in=round(height_in, 2),
        caveats=caveats,
    )
