"""Constants for the Azimuth / Doench Rule Set 2 on-target scorer.

Rule Set 2 (Doench et al. 2016, often called "Azimuth") scores a 30-nt window, NOT a bare
20-nt protospacer. The canonical window layout used by the Azimuth package and the Broad GPP
sgRNA designer is:

    [0:4]   4 nt 5' genomic context
    [4:24]  20 nt protospacer
    [24:27] 3 nt PAM (NGG)
    [27:30] 3 nt 3' genomic context

so the protospacer sits at offset 4. Feeding a padded or fabricated context would violate
"AI never fabricates biology" -- callers must supply the real 30-mer.
"""

from __future__ import annotations

from typing import Literal

THIRTYMER_LENGTH = 30

# VERIFY: 30-mer = 4 nt 5' context + 20 nt protospacer + 3 nt PAM + 3 nt 3' context, so the
# protospacer starts at index 4. Confirm against the Azimuth README / Doench 2016 before
# relying on the offset consistency guard in score_guide_on_target.
PROTOSPACER_OFFSET = 4

# The two trained pickles shipped in the upstream Azimuth repo. The sequence-only "nopos"
# model is the honest default for guide-only requests; the "full" model additionally needs the
# cut position + percent-peptide of the target site, which a bare guide does not carry.
AzimuthModel = Literal["V3_model_nopos", "V3_model_full"]
SUPPORTED_MODELS: tuple[AzimuthModel, ...] = ("V3_model_nopos", "V3_model_full")
DEFAULT_MODEL: AzimuthModel = "V3_model_nopos"
