"""Off-target scoring algorithms — published deterministic formulas.

We use Hsu 2013's MIT off-target score as the primary specificity metric. It
weights mismatches by position along the 20-nt protospacer: PAM-proximal
("seed") mismatches penalize hard, PAM-distal mismatches penalize lightly.
The score is a deterministic transformation of the published weights — no ML,
no fitting. The weights themselves are reproduced verbatim from the Hsu et al.
2013 supplementary materials and are the same ones used by every major CRISPR
design tool (CHOPCHOP, CRISPOR, MIT CRISPR Design Tool).

We also expose a simpler per-mismatch list so the agent / UI can explain
WHERE the mismatches sit, not just produce a single number.

# CFD scoring (Doench 2016) — now shipped

`cfd_score` / `cfd_mismatch_component` implement Doench's base-identity-weighted CFD score
(the modern field standard, slightly outperforms MIT). The 240 mismatch weights + 16 PAM
weights load from a committed, checksummed data file (`data/cfd_doench2016.json`) sourced
verbatim from CRISPOR's CFD_Scoring (a faithful redistribution of Doench 2016 Supp. Table 19)
— NEVER transcribed from memory. The full CFD needs the off-target's PAM; where the PAM is
unverified (the BLAST-based off-target search does not fetch flanking context) we report the
mismatch-tolerance component only, clearly labelled.

# What this file is NOT

  - **Bulge / indel-aware scoring.** This module assumes substitution-only
    mismatches, consistent with how the upstream BLAST tool reports them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# Hsu 2013 Supplementary Table — per-position mismatch weights for SpCas9.
# Index 0 = 5' end of the 20-nt protospacer (PAM-distal).
# Index 19 = PAM-adjacent base (seed region, most disruptive).
# Source: Hsu PD et al. (2013) Nat Biotechnol 31:827-832, Supplementary
# Materials. These values are reproduced verbatim across major CRISPR tools.
HSU_2013_WEIGHTS: tuple[float, ...] = (
    0.000,
    0.000,
    0.014,
    0.000,
    0.000,
    0.395,
    0.317,
    0.000,
    0.389,
    0.079,
    0.445,
    0.508,
    0.613,
    0.851,
    0.732,
    0.828,
    0.615,
    0.804,
    0.685,
    0.583,
)

assert len(HSU_2013_WEIGHTS) == 20, "Hsu 2013 weights must be 20 positions"


@dataclass
class OfftargetScore:
    """Result of MIT-style off-target scoring for one off-target candidate.

    `score` is in [0, 1]: 0 = no risk (every position has a maximally-disrupting
    mismatch), 1 = perfect match (no mismatches). Values >0.2 typically signal
    real off-target risk in the literature.
    """

    mismatch_positions: list[int]
    """1-based positions (1=5' end, 20=PAM-adjacent) where the off-target differs from the guide."""

    score: float
    """MIT off-target score in [0, 1]. Higher = greater off-target cleavage likelihood."""

    used_full_alignment: bool
    """True if we computed positions from the alignment strings. False if we fell back
    to a count-only approximation because the alignment was unavailable."""


def positions_from_alignment(
    *,
    guide_seq: str,
    query_aligned: str,
    subject_aligned: str,
) -> list[int]:
    """Identify the 1-based positions of mismatches in the guide.

    The BLAST alignment reports query/subject strings that may contain gaps
    ('-'). We treat gaps as mismatches at the corresponding guide position.

    The alignment may also cover only part of the guide — if the alignment
    starts at guide position 3 (because BLAST trimmed the 5' end), we offset
    accordingly. Positions outside the aligned region are NOT flagged as
    mismatches here (the upstream tool reports query_coverage_percent
    separately).

    Returns 1-based positions where guide_seq and subject_aligned disagree.
    """
    if not query_aligned or not subject_aligned:
        return []
    if len(query_aligned) != len(subject_aligned):
        return []

    # Walk the alignment. Track the index into the original guide_seq via the
    # non-gap query characters. A mismatch is any position where query != subject
    # AND query is not a gap (we want mismatches relative to the GUIDE, not the
    # subject; gaps in the query mean the subject has an insertion which is a
    # different signal than a substitution).
    guide_index = 0
    mismatches: list[int] = []
    for q_char, s_char in zip(query_aligned.upper(), subject_aligned.upper(), strict=True):
        if q_char == "-":
            # Subject has an extra base. Not a substitution in the guide;
            # we don't surface it as a position-weighted penalty.
            continue
        guide_index += 1
        if s_char == "-":
            # Subject has a deletion. Penalize this position as a mismatch —
            # the guide base has no partner on the off-target.
            mismatches.append(guide_index)
            continue
        if q_char != s_char:
            mismatches.append(guide_index)

    # Cap to guide length defensively. If BLAST returned a longer alignment
    # than the guide (unusual), positions past len(guide_seq) are ignored.
    return [p for p in mismatches if 1 <= p <= len(guide_seq)]


def mit_score_from_positions(positions: list[int], guide_length: int = 20) -> float:
    """Compute the MIT off-target score from mismatch positions.

    score = product over mismatched positions of (1 - W[pos])

    Where W is the Hsu 2013 weight indexed from the PAM-distal 5' end.

    Returns 1.0 for a perfect match. Returns 0.0 only if some position has
    weight = 1.0 (none do in the published table, so a real 0 would require
    every position to mismatch — practically impossible).

    Guides shorter than 20 nt are scored against the PAM-PROXIMAL end of the
    weight vector (the seed must align with the seed weights — that's the
    biologically conserved bit).
    """
    if not positions:
        return 1.0
    if guide_length <= 0:
        return 0.0
    # Align the guide to the PAM-proximal end of the 20-position weight table.
    # For a 20-nt guide this is a no-op. For shorter guides (e.g. 18 nt), the
    # last guide position maps to weight index 19 (PAM-adjacent), and earlier
    # positions shift right accordingly.
    weight_offset = 20 - guide_length

    score = 1.0
    for pos in positions:
        if pos < 1 or pos > guide_length:
            continue
        weight_index = (pos - 1) + weight_offset
        if 0 <= weight_index < 20:
            score *= 1.0 - HSU_2013_WEIGHTS[weight_index]
    return max(0.0, min(1.0, score))


def score_offtarget(
    *,
    guide_seq: str,
    query_aligned: str,
    subject_aligned: str,
    mismatch_count_fallback: int = 0,
) -> OfftargetScore:
    """Compute the MIT off-target score for one BLAST hit.

    Prefers the full alignment-based path. Falls back to a count-only
    approximation if the alignment strings are missing (older BLAST outputs,
    test fixtures): assumes the mismatches are distributed evenly across the
    PAM-distal half, which is the most-optimistic interpretation and produces
    a score that may UNDER-estimate risk. The caller surfaces a caveat in that
    case.
    """
    positions = positions_from_alignment(
        guide_seq=guide_seq,
        query_aligned=query_aligned,
        subject_aligned=subject_aligned,
    )
    if positions:
        return OfftargetScore(
            mismatch_positions=positions,
            score=mit_score_from_positions(positions, guide_length=len(guide_seq)),
            used_full_alignment=True,
        )
    # Fallback: place all mismatches at the PAM-distal end (weights = 0 there)
    # to produce an optimistic score. The find_offtargets caveat list flags this.
    fallback_positions = list(range(1, mismatch_count_fallback + 1))
    return OfftargetScore(
        mismatch_positions=fallback_positions,
        score=mit_score_from_positions(fallback_positions, guide_length=len(guide_seq)),
        used_full_alignment=False,
    )


# --- CFD off-target score (Doench 2016) ---------------------------------------------
#
# CFD weights each mismatch by its position AND its specific base pairing (an rG:dT at
# position 15 differs from an rG:dA), unlike MIT which is position-only. Data is loaded from
# a committed, checksummed JSON sourced verbatim from CRISPOR's CFD_Scoring — see the module
# docstring + the JSON's `_provenance` block.

_CFD_DATA_PATH = Path(__file__).parent / "data" / "cfd_doench2016.json"

# Single-base "revcom" (Doench's basecomp) used to build the CFD mismatch key's DNA ('d') base.
_CFD_COMPLEMENT = {"A": "T", "C": "G", "G": "C", "T": "A", "U": "A"}


@lru_cache(maxsize=1)
def _cfd_tables() -> tuple[dict[str, float], dict[str, float]]:
    """Load the committed Doench-2016 CFD tables: (mismatch_scores, pam_scores).

    Sourced verbatim (never transcribed); provenance + sha256 live in the JSON header.
    """
    data = json.loads(_CFD_DATA_PATH.read_text(encoding="utf-8"))
    return data["mismatch_scores"], data["pam_scores"]


def cfd_mismatch_component(on_target: str, off_target: str) -> float:
    """CFD's mismatch-tolerance component: the product over mismatched positions of the
    Doench-2016 per-(position, sgRNA-base, target-base) weight. NO PAM factor — the
    sequence-only part of CFD, for when the off-target PAM is unknown/unverified.

    Replicates CRISPOR/Doench `calc_cfd` exactly (minus the trailing PAM factor): after T->U
    on both sequences, a mismatch contributes `mm['r'+sgRNA_base+':d'+complement(target_base)+
    ','+position]`. Raises on length mismatch or any base/position absent from the published
    table — it never silently scores a malformed input.
    """
    mm, _ = _cfd_tables()
    wt = on_target.upper().replace("T", "U")
    sg = off_target.upper().replace("T", "U")
    if len(wt) != len(sg):
        raise ValueError(f"CFD needs equal-length sequences; got {len(wt)} vs {len(sg)}.")
    score = 1.0
    for i, sl in enumerate(sg):
        if wt[i] == sl:
            continue
        d = _CFD_COMPLEMENT.get(sl)
        if d is None:
            raise ValueError(f"CFD: non-ACGT base {sl!r} in off-target at position {i + 1}.")
        key = f"r{wt[i]}:d{d},{i + 1}"
        try:
            score *= mm[key]
        except KeyError as e:
            raise ValueError(f"CFD: no published weight for {key!r} (verify the input bases).") from e
    return score


def cfd_score(on_target: str, off_target: str, pam: str) -> float:
    """Full CFD off-target score (Doench 2016) = mismatch component x PAM-activity weight.

    `pam` is the off-target's 2-nt PAM (the GG of NGG, i.e. `off_target_23mer[-2:]` in Doench's
    convention). 1.0 = identical sequence with a fully-active PAM. Raises if the PAM is not in
    the published 16-entry table.
    """
    _, pam_scores = _cfd_tables()
    pam_key = pam.upper().replace("U", "T")
    if pam_key not in pam_scores:
        raise ValueError(f"CFD: PAM {pam!r} not in the published PAM table (expected 2 nt, e.g. 'GG').")
    return cfd_mismatch_component(on_target, off_target) * pam_scores[pam_key]
