"""Structure tools — first Phase 4 package.

Tools that fetch and reason over 3D protein structures: AlphaFold predictions,
PDB experimental structures, structural features (later: InterPro domains).

Imports here run for their registration side effects — the module-level
`@register_tool` decorator on each handler adds it to `bioforge.tools.registry.REGISTRY`.
"""

from bioforge.tools.structure import (  # noqa: F401
    compare_structures,
    fetch_alphafold,
    fetch_interpro,
    fetch_pdb,
    find_best,
)
