"""Off-target PAM extraction — the missing piece for the FULL Doench-2016 CFD score.

`find_offtargets` BLASTs a guide's 20-nt protospacer; a hit says WHERE the protospacer matches
but not whether a Cas9 PAM (NGG) sits 3' of it in the genome. CFD = mismatch-tolerance x
PAM-activity (see `offtarget_scoring.cfd_score`), so the full score needs the off-target's PAM —
the genomic bases immediately 3' of the off-target protospacer, ON THE MATCHING STRAND. That
requires reading the flank, which BLAST does not return.

This module is two parts:

  - `extract_pam` — PURE coordinate/strand arithmetic over a fetched plus-strand window, plus a
    SOUNDNESS GATE: it reconstructs the off-target protospacer from the window and requires it to
    equal what BLAST reported. If the arithmetic is ever wrong (off-by-one, flipped strand), the
    reconstruction won't match and we return None — so a bug degrades to "no PAM, mismatch-only
    CFD" rather than a CONFIDENTLY-WRONG PAM. That is the §0 contract: a wrong PAM is worse than
    no PAM, so we refuse to emit one we cannot verify.

  - `efetch_flank` — the NCBI Entrez efetch call (network), injected in tests so the suite never
    hits the wire.

Substitution-only: only clean, gap-free, full-length (guide_len) hits are handled — the same
envelope `find_offtargets` already uses for the CFD mismatch component.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# Plus-strand DNA complement for the minus-strand reconstruction. Local + tiny on purpose:
# this is a string helper, not the rev_comp *tool* (which returns a typed ToolOutput).
_COMPLEMENT = str.maketrans("ACGTacgt", "TGCAtgca")

# Extra plus-strand bases fetched on each side of the protospacer so the 3-nt PAM (either
# strand) is always inside the window, with a little buffer.
FLANK_MARGIN = 6


class OfftargetPamError(Exception):
    """Raised when the efetch flank fetch fails or returns something unparseable."""


@dataclass(frozen=True)
class PamExtraction:
    """A verified off-target PAM. Only constructed when the soundness gate passed."""

    protospacer: str  # off-target protospacer, 5'->3' on the matching strand (reconstructed)
    pam3: str  # the 3-nt PAM (NGG), 5'->3'
    pam2: str  # the GG of NGG — what cfd_score consumes
    strand: str  # "plus" | "minus"


def _revcomp(seq: str) -> str:
    return seq.translate(_COMPLEMENT)[::-1]


def extract_pam(
    *,
    window_plus: str,
    window_start: int,
    subject_start: int,
    subject_end: int,
    guide_len: int,
    expected_protospacer: str,
) -> PamExtraction | None:
    """Reconstruct the off-target protospacer + PAM from a plus-strand genomic window.

    Parameters
    ----------
    window_plus : plus-strand genomic sequence covering the locus + flanks.
    window_start: 1-based plus-strand coordinate of `window_plus[0]`.
    subject_start / subject_end : BLAST subject coords (1-based). Biopython orders them by query
        direction, so a plus-strand hit has start < end and a MINUS-strand hit has start > end.
    guide_len : protospacer length (e.g. 20). Only clean, gap-free, full-length hits reach here.
    expected_protospacer : the off-target protospacer as it pairs with the guide (the BLAST
        subject_aligned, ungapped, 5'->3') — the soundness check.

    Returns a `PamExtraction`, or None when the window is too short, the PAM is not 3 clean ACGT
    bases, the span is not exactly `guide_len`, or — the soundness gate — the reconstructed
    protospacer does not equal `expected_protospacer`.
    """
    lo = min(subject_start, subject_end)
    hi = max(subject_start, subject_end)
    if hi - lo + 1 != guide_len:
        return None  # gapped / partial span — outside the substitution-only envelope
    is_minus = subject_start > subject_end

    def _plus(coord_lo: int, coord_hi: int) -> str | None:
        """Plus-strand bases at 1-based inclusive [coord_lo, coord_hi], or None if out of window."""
        i = coord_lo - window_start
        j = coord_hi - window_start
        if i < 0 or j >= len(window_plus) or i > j:
            return None
        return window_plus[i : j + 1].upper()

    proto_plus = _plus(lo, hi)
    if proto_plus is None:
        return None

    if is_minus:
        # Protospacer 3' end is at the LOWER plus coord (lo); the PAM is 3 nt further 3' on the
        # minus strand == lower plus coords [lo-3, lo-1], reverse-complemented to 5'->3'.
        protospacer = _revcomp(proto_plus)
        pam_plus = _plus(lo - 3, lo - 1)
        pam3 = _revcomp(pam_plus) if pam_plus is not None else None
        strand = "minus"
    else:
        # Protospacer reads 5'->3' on the plus strand; the PAM is plus coords [hi+1, hi+3].
        protospacer = proto_plus
        pam3 = _plus(hi + 1, hi + 3)
        strand = "plus"

    if pam3 is None or len(pam3) != 3 or (set(pam3) - set("ACGT")):
        return None
    # SOUNDNESS GATE — the reconstructed protospacer must match what BLAST said is here. A failure
    # means our coords/strand are wrong, so we refuse the PAM rather than emit a wrong one.
    if protospacer != expected_protospacer.upper():
        return None
    return PamExtraction(protospacer=protospacer, pam3=pam3, pam2=pam3[1:], strand=strand)


# (accession, seq_start, seq_stop, email) -> raw FASTA text
EfetchFn = Callable[[str, int, int, str], str]


def _default_efetch(accession: str, seq_start: int, seq_stop: int, email: str) -> str:
    """Fetch plus-strand [seq_start, seq_stop] (1-based inclusive) of `accession` from NCBI nuccore.

    Real network path. Biopython Bio.Entrez.efetch with rettype=fasta returns a FASTA record for
    the requested subrange (always plus strand — orientation is handled in `extract_pam`).
    """
    from Bio import Entrez

    Entrez.email = email or None  # NCBI asks for an email; empty -> anonymous (may rate-limit)
    handle = Entrez.efetch(
        db="nuccore",
        id=accession,
        rettype="fasta",
        retmode="text",
        seq_start=str(seq_start),
        seq_stop=str(seq_stop),
    )
    try:
        return handle.read()
    finally:
        handle.close()


def efetch_flank(
    *,
    accession: str,
    seq_start: int,
    seq_stop: int,
    email: str,
    efetch_fn: EfetchFn | None = None,
) -> str:
    """Return the bare plus-strand sequence for [seq_start, seq_stop], FASTA header stripped.

    `efetch_fn` is injected in tests. Raises `OfftargetPamError` on any failure or empty result so
    the caller can fall back to the mismatch-only CFD component.
    """
    fn = efetch_fn if efetch_fn is not None else _default_efetch
    lo, hi = (seq_start, seq_stop) if seq_start <= seq_stop else (seq_stop, seq_start)
    try:
        raw = fn(accession, lo, hi, email)
    except Exception as e:  # network / parse / HTTP — surface as one typed error
        raise OfftargetPamError(f"efetch failed for {accession}:{lo}-{hi}: {type(e).__name__}: {e}") from e
    seq = "".join(line.strip() for line in raw.splitlines() if line and not line.startswith(">"))
    if not seq:
        raise OfftargetPamError(f"efetch returned no sequence for {accession}:{lo}-{hi}.")
    return seq.upper()
