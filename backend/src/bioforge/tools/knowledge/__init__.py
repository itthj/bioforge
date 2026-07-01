"""Knowledge retrieval tools — literature, gene info, networks, disease associations.

These tools bring external biological knowledge directly into the agent loop,
answering the "what is known about X" class of questions without the analyst
leaving BioForge.
"""

from bioforge.tools.knowledge import (  # noqa: F401
    drug_gene_interaction,
    fetch_gene_info,
    fetch_uniprot,
    gwas_catalog,
    hpo_phenotype,
    open_targets,
    protein_properties,
    restriction_sites,
    search_pubmed,
    string_network,
)

__all__ = [
    "drug_gene_interaction",
    "fetch_gene_info",
    "fetch_uniprot",
    "gwas_catalog",
    "hpo_phenotype",
    "open_targets",
    "protein_properties",
    "restriction_sites",
    "search_pubmed",
    "string_network",
]
