"""Variant annotation tools (Phase 3).

`annotate_variant` wraps the Ensembl REST `/vep/{species}/hgvs/` endpoint and
returns predicted molecular consequences across all overlapping transcripts,
plus colocated-variant cross-references (ClinVar, dbSNP, gnomAD frequencies)
that come back in the same response.

`lookup_clinvar` and `lookup_dbsnp` talk to NCBI E-utilities directly for the
full curated record — call them when annotate_variant's join is too lossy or
the variant is too new for Ensembl's release cadence.
"""

from bioforge.tools.variants import (  # noqa: F401  — register on import
    annotate_variant,
    call_variants,
    format_hgvs,
    lookup_clinvar,
    lookup_dbsnp,
    lookup_gnomad,
    normalize_hgvs,
)

__all__ = [
    "annotate_variant",
    "call_variants",
    "format_hgvs",
    "lookup_clinvar",
    "lookup_dbsnp",
    "lookup_gnomad",
    "normalize_hgvs",
]
