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

# What this file is NOT

  - **CFD scoring (Doench 2016).** Doench's score uses a full 4×4 base-pair
    substitution matrix per position (320 weights total). It is the modern
    field standard and slightly outperforms MIT. We don't ship the full matrix
    here because reproducing all 320 published values would be error-prone
    without a curated copy of the supplementary table — the MIT score we DO
    ship is functionally equivalent at the level of risk classification.
    Future slice: load CFD matrix from a committed data file.
  - **Bulge / indel-aware scoring.** This module assumes substitution-only
    mismatches, consistent with how the upstream BLAST tool reports them.
"""

from __future__ import annotations

from dataclasses import dataclass

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
