"""RNA-seq differential expression analysis using PyDESeq2.

Differential expression analysis is the single most common bioinformatics
analysis in modern biology. DESeq2 is the gold-standard method, used in
~60,000 publications. PyDESeq2 is a faithful Python reimplementation that
produces results concordant with the R package.

This tool accepts a count matrix (genes × samples) and a sample condition
table, runs DESeq2 normalisation + negative binomial GLM + Wald test, and
returns the full results table with:
  - log2FoldChange (shrunken with apeglm)
  - p-value and adjusted p-value (Benjamini-Hochberg)
  - baseMean, lfcSE, stat

Inputs are provided as JSON-serialisable dicts (count matrix) and a condition
mapping (sample name → condition label). This makes the tool callable from
natural-language workflows without file uploads.

Requirements: pydeseq2 (pip install pydeseq2 --break-system-packages)
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import Field, field_validator

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool


class DEResult(ToolOutput):
    gene: str
    base_mean: float = Field(description="Mean of normalised counts across all samples.")
    log2_fold_change: float = Field(description="Shrunken log2 fold change (apeglm).")
    lfc_se: float = Field(description="Standard error of the log2 fold change.")
    stat: float = Field(description="Wald test statistic.")
    p_value: float
    p_adj: float = Field(description="Benjamini-Hochberg adjusted p-value.")
    significant: bool = Field(description="True if padj < 0.05 and |log2FC| > 1.")


class DifferentialExpressionInput(ToolInput):
    counts: dict[str, dict[str, int]] = Field(
        ...,
        description=(
            "Count matrix as a nested dict: {gene_id: {sample_id: count}}. "
            "Example: {'BRCA1': {'ctrl_1': 245, 'ctrl_2': 312, 'treat_1': 89, 'treat_2': 102}}. "
            "Must have at least 2 samples per condition and at least 3 genes. "
            "Raw integer counts only — do not pre-normalise."
        ),
    )
    conditions: dict[str, str] = Field(
        ...,
        description=(
            "Sample-to-condition mapping: {sample_id: condition_label}. "
            "Exactly 2 unique condition labels required. "
            "Example: {'ctrl_1': 'control', 'ctrl_2': 'control', "
            "'treat_1': 'treatment', 'treat_2': 'treatment'}."
        ),
    )
    reference_condition: str = Field(
        ...,
        description=(
            "The baseline/control condition label (denominator of the fold change). "
            "Example: 'control'. Log2FC > 0 means higher expression in the OTHER condition."
        ),
    )
    p_adj_threshold: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Adjusted p-value significance threshold. Default 0.05.",
    )
    lfc_threshold: float = Field(
        default=1.0,
        ge=0.0,
        description="Absolute log2 fold-change threshold for 'significant' flag. Default 1.0 (= 2-fold).",
    )
    max_results: int = Field(
        default=50,
        ge=1,
        le=5000,
        description="Maximum rows to return, ranked by adjusted p-value.",
    )

    @field_validator("counts")
    @classmethod
    def validate_counts(cls, v: dict) -> dict:
        if len(v) < 3:
            raise ValueError("Count matrix must have at least 3 genes.")
        return v

    @field_validator("conditions")
    @classmethod
    def validate_conditions(cls, v: dict) -> dict:
        unique = set(v.values())
        if len(unique) != 2:
            raise ValueError(
                f"Exactly 2 unique conditions required; got {len(unique)}: {unique}."
            )
        return v


class DifferentialExpressionOutput(ToolOutput):
    n_genes_tested: int
    n_significant: int = Field(description="Genes significant at padj < threshold AND |log2FC| > lfc_threshold.")
    n_upregulated: int
    n_downregulated: int
    comparison: str = Field(description="Human-readable comparison string, e.g. 'treatment vs control'.")
    results: list[DEResult]
    method: str = Field(default="PyDESeq2 (DESeq2 negative binomial GLM, Wald test, BH correction)")
    caveats: list[str] = Field(default_factory=list)


def _run_deseq2_sync(
    counts: dict[str, dict[str, int]],
    conditions: dict[str, str],
    reference_condition: str,
) -> list[dict[str, Any]]:
    """Run PyDESeq2 synchronously (called in a thread pool)."""
    try:
        import numpy as np
        import pandas as pd
        from pydeseq2.dds import DeseqDataSet
        from pydeseq2.ds import DeseqStats
    except ImportError as e:
        raise ToolError(
            f"PyDESeq2 is not installed: {e}. "
            "Install with: pip install pydeseq2"
        ) from e

    # Build count matrix DataFrame (samples × genes)
    sample_ids = list(conditions.keys())
    gene_ids   = list(counts.keys())

    count_matrix = pd.DataFrame(
        {gene: [counts[gene].get(s, 0) for s in sample_ids] for gene in gene_ids},
        index=sample_ids,
    )
    # DESeq2 requires integer counts
    count_matrix = count_matrix.astype(int)

    # Filter lowly-expressed genes (at least 10 counts total)
    count_matrix = count_matrix.loc[:, count_matrix.sum() >= 10]
    if count_matrix.shape[1] == 0:
        raise ToolError("All genes were filtered out (total count < 10). Check that counts are raw integers.")

    metadata = pd.DataFrame(
        {"condition": [conditions[s] for s in sample_ids]},
        index=sample_ids,
    )

    # Run DESeq2
    dds = DeseqDataSet(
        counts=count_matrix,
        metadata=metadata,
        design_factors="condition",
        ref_level=["condition", reference_condition],
        refit_cooks=True,
    )
    dds.deseq2()

    stat_res = DeseqStats(dds, contrast=["condition", None, reference_condition])
    stat_res.summary()

    df = stat_res.results_df.copy()
    df.index.name = "gene"
    df = df.reset_index()
    df = df.sort_values("padj", na_position="last")

    records = []
    for _, row in df.iterrows():
        records.append({
            "gene": str(row["gene"]),
            "base_mean": float(row.get("baseMean", 0.0)),
            "log2_fold_change": float(row.get("log2FoldChange", 0.0)),
            "lfc_se": float(row.get("lfcSE", 0.0)),
            "stat": float(row.get("stat", 0.0)),
            "p_value": float(row.get("pvalue", 1.0)) if not pd.isna(row.get("pvalue")) else 1.0,
            "p_adj": float(row.get("padj", 1.0)) if not pd.isna(row.get("padj")) else 1.0,
        })

    return records


@register_tool(
    name="differential_expression",
    description=(
        "Run RNA-seq differential expression analysis on a raw count matrix "
        "using DESeq2 (via PyDESeq2 Python implementation). Accepts a count "
        "matrix (genes × samples) and a sample-condition mapping, then returns "
        "log2 fold changes, adjusted p-values, and significance calls. Use when "
        "the user provides RNA-seq count data and asks 'find differentially "
        "expressed genes between conditions', 'run DESeq2 on these counts', "
        "or 'which genes are upregulated in treated samples'. Works for any "
        "two-condition comparison. Requires raw integer counts (not RPKM/TPM). "
        "For large datasets (>10,000 genes), consider providing a pre-filtered "
        "count matrix for faster runtime."
    ),
    input_model=DifferentialExpressionInput,
    output_model=DifferentialExpressionOutput,
    version="1.0.0",
    citations=[
        "Love MI, Huber W, Anders S (2014) Moderated estimation of fold change "
        "and dispersion for RNA-seq data with DESeq2. Genome Biology 15:550.",
        "Muzellec B et al. (2023) PyDESeq2: a python package for bulk RNA-seq "
        "differential expression analysis. Bioinformatics 39(9):btad547.",
    ],
    cost_hint="moderate",
    tags=["functional", "rnaseq", "differential_expression", "deseq2", "transcriptomics"],
    published_accuracy={
        "concordance_with_deseq2_r": "Muzellec et al. 2023: Pearson r > 0.999 for log2FC and padj on benchmark datasets",
    },
)
async def differential_expression(inp: DifferentialExpressionInput) -> DifferentialExpressionOutput:
    # Determine the comparison label
    conditions_set = set(inp.conditions.values())
    other_condition = next(c for c in conditions_set if c != inp.reference_condition)
    comparison = f"{other_condition} vs {inp.reference_condition}"

    # Run in thread pool (pydeseq2 is synchronous)
    loop = asyncio.get_event_loop()
    try:
        records = await loop.run_in_executor(
            None,
            _run_deseq2_sync,
            inp.counts,
            inp.conditions,
            inp.reference_condition,
        )
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(f"DESeq2 analysis failed: {type(e).__name__}: {e}") from e

    # Build output
    results: list[DEResult] = []
    n_sig = n_up = n_down = 0

    for rec in records[:inp.max_results]:
        sig = (
            rec["p_adj"] < inp.p_adj_threshold
            and abs(rec["log2_fold_change"]) >= inp.lfc_threshold
        )
        if sig:
            n_sig += 1
            if rec["log2_fold_change"] > 0:
                n_up += 1
            else:
                n_down += 1
        results.append(DEResult(
            gene=rec["gene"],
            base_mean=round(rec["base_mean"], 3),
            log2_fold_change=round(rec["log2_fold_change"], 4),
            lfc_se=round(rec["lfc_se"], 4),
            stat=round(rec["stat"], 4),
            p_value=rec["p_value"],
            p_adj=rec["p_adj"],
            significant=sig,
        ))

    caveats = [
        "DESeq2 assumes count data follows a negative binomial distribution. "
        "Results are most reliable with at least 3 replicates per condition.",
        f"'Significant' is defined as padj < {inp.p_adj_threshold} AND "
        f"|log2FC| ≥ {inp.lfc_threshold} ({2**inp.lfc_threshold:.1f}-fold change). "
        "Adjust thresholds as appropriate for your experiment.",
        "Genes with total counts < 10 across all samples were filtered before analysis.",
        "This analysis treats all samples as independent biological replicates. "
        "If your data has paired samples or batch effects, a more complex design "
        "formula is needed (not currently supported by this tool).",
    ]

    return DifferentialExpressionOutput(
        n_genes_tested=len(records),
        n_significant=n_sig,
        n_upregulated=n_up,
        n_downregulated=n_down,
        comparison=comparison,
        results=results,
        caveats=caveats,
    )
