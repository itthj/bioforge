"""Functional analysis tools — enrichment, pathways, differential expression.

Tools that move from a list of genes to biological interpretation:
GO enrichment, KEGG/Reactome pathways, and RNA-seq differential expression.
"""

from bioforge.tools.functional import (  # noqa: F401
    differential_expression,
    go_enrichment,
)

__all__ = [
    "differential_expression",
    "go_enrichment",
]
