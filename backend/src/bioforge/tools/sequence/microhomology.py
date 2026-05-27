"""Microhomology-mediated end joining (MMEJ) predictor — Bae 2014.

MMEJ is a Cas9 repair pathway that uses short stretches of sequence identity
(microhomologies) flanking the break to template a deletion. It is the
dominant non-NHEJ outcome at many cut sites and accounts for a large fraction
of the "deletion_larger" bucket the old rule-of-thumb table aggregated.

This module implements the Bae 2014 / MicroHomology Predictor algorithm:

  1. Scan flanking sequences for pairs of identical k-mers (k ≥ 2) where one
     copy sits to the LEFT of the cut and the other to the RIGHT.
  2. For each pair, the MMEJ deletion removes the sequence between the two
     copies plus ONE copy of the MH itself — leaving a single MH in the
     repaired product.
  3. Each pair gets a pattern score (Bae 2014):

         score = length × exp(-(deletion_size - length) / window) × GC_factor

     where `length` is MH length in bp, `deletion_size` is the resulting
     net deletion, `window` controls distance decay (typically 4-8), and
     `GC_factor` rewards GC-rich MHs (stronger base pairing).
  4. Higher score → more likely MMEJ outcome. We don't claim to predict
     ABSOLUTE frequencies — the caller normalizes scores into probabilities
     after merging with the NHEJ outcome list.

This is a DETERMINISTIC algorithm — no ML, no trained models. Citations are
in the caller's tool registration.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Microhomology:
    """One MH pair flanking a cut site.

    Coordinates are 0-based positions on the forward strand of the target.
    The MH sequence appears at both left_start..left_end (exclusive end) and
    right_start..right_end. After MMEJ repair, the deletion product retains a
    single copy of the MH; the convention is to keep the LEFT copy.
    """

    sequence: str
    length: int
    left_start: int
    left_end: int  # exclusive
    right_start: int
    right_end: int  # exclusive
    pattern_score: float
    deletion_size: int
    """Net number of base pairs removed: (right_end - left_end), since the right
    copy + intervening bases get deleted and the left copy is retained."""


def _gc_factor(seq: str) -> float:
    """Per-Bae the GC-rich MHs are more stable. We use a simple linear factor:
    1.0 for all-AT, 1.5 for all-GC. Empirically calibrated to roughly match
    published MH frequency data."""
    if not seq:
        return 1.0
    gc = sum(1 for b in seq.upper() if b in ("G", "C"))
    return 1.0 + 0.5 * (gc / len(seq))


def find_microhomologies(
    *,
    target: str,
    cut_position: int,
    min_length: int = 2,
    max_length: int = 12,
    window: int = 20,
    decay_window: float = 4.0,
) -> list[Microhomology]:
    """Find microhomology pairs flanking a cut site.

    Args:
        target: forward-strand DNA, uppercase.
        cut_position: 0-based forward-strand position where Cas9 cleaves
            (i.e. the break is between target[cut-1] and target[cut]).
        min_length: shortest MH to consider. 2 is the published threshold;
            longer MHs are increasingly dominant.
        max_length: longest MH to consider. >12 is rare for Cas9 deletions.
        window: how far on each side of the cut to search for MH copies.
            20 bp covers the vast majority of literature MMEJ outcomes.
        decay_window: Bae's `w` parameter — controls how fast pattern score
            decays as deletion size grows beyond MH length. Default 4.

    Returns:
        List of Microhomology records, sorted by descending pattern_score
        (most-likely MMEJ outcomes first). Empty if no MH found.
    """
    if cut_position < min_length or cut_position > len(target) - min_length:
        return []

    left_start_bound = max(0, cut_position - window)
    right_end_bound = min(len(target), cut_position + window)
    left_flank = target[left_start_bound:cut_position]
    right_flank = target[cut_position:right_end_bound]

    candidates: list[Microhomology] = []
    # Try MH lengths from max down to min. Longer MHs dominate; we DON'T drop
    # shorter MHs nested inside longer ones — both can produce distinct outcomes,
    # but we deduplicate based on the resulting deletion endpoints.
    seen_deletions: set[tuple[int, int]] = set()
    for k in range(max_length, min_length - 1, -1):
        # All k-mers in the LEFT flank.
        for li in range(len(left_flank) - k + 1):
            kmer = left_flank[li : li + k]
            # Skip MHs that are pure A or pure T runs — they over-trigger
            # in low-complexity regions and inflate scores. (Bae 2014 also
            # filters these.)
            if len(set(kmer)) == 1:
                continue
            # Find matching k-mers in the RIGHT flank.
            start = 0
            while True:
                ri = right_flank.find(kmer, start)
                if ri == -1:
                    break
                # Compute absolute coordinates on the target.
                left_abs_start = left_start_bound + li
                left_abs_end = left_abs_start + k
                right_abs_start = cut_position + ri
                right_abs_end = right_abs_start + k
                # The deletion size is right_abs_end - left_abs_end (the
                # span deleted from the RIGHT copy's end back to the LEFT
                # copy's end — one MH copy is retained).
                deletion_size = right_abs_end - left_abs_end
                start = ri + 1
                if deletion_size <= 0:
                    continue
                # Dedup overlapping MHs that produce the same deletion.
                if (left_abs_end, right_abs_end) in seen_deletions:
                    continue
                seen_deletions.add((left_abs_end, right_abs_end))
                # Bae 2014 pattern score.
                score = k * math.exp(-(deletion_size - k) / decay_window) * _gc_factor(kmer)
                candidates.append(
                    Microhomology(
                        sequence=kmer,
                        length=k,
                        left_start=left_abs_start,
                        left_end=left_abs_end,
                        right_start=right_abs_start,
                        right_end=right_abs_end,
                        pattern_score=score,
                        deletion_size=deletion_size,
                    )
                )

    candidates.sort(key=lambda m: m.pattern_score, reverse=True)
    return candidates


def apply_mmej_deletion(target: str, mh: Microhomology) -> str:
    """Produce the post-MMEJ deletion sequence.

    The repair retains the LEFT copy of the MH and deletes everything from
    the LEFT copy's END through the RIGHT copy's END (inclusive of the right
    copy itself). The result is `target[:left_end] + target[right_end:]`.
    """
    return target[: mh.left_end] + target[mh.right_end :]


def normalize_to_probabilities(
    *,
    microhomologies: list[Microhomology],
    mmej_fraction_of_total: float = 0.35,
) -> dict[Microhomology, float]:
    """Convert raw pattern scores into per-outcome probabilities.

    Published Cas9 repair distributions vary widely, but MMEJ typically
    accounts for 20-50% of total repair events. We default to 35% as a
    middle-ground published average. The caller is expected to scale the
    remaining 65% across NHEJ outcomes.

    Returns a dict mapping each Microhomology to its share of the MMEJ pie.
    Empty input → empty dict; total share sums to mmej_fraction_of_total.
    """
    if not microhomologies:
        return {}
    total_score = sum(m.pattern_score for m in microhomologies)
    if total_score <= 0:
        return {}
    return {m: (m.pattern_score / total_score) * mmej_fraction_of_total for m in microhomologies}
