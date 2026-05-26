"""Tests for find_orfs.

Test sequences are constructed to have known ORFs at known positions so coordinates and
filters can be verified deterministically. The translate logic itself is covered by
test_translate.py — these tests focus on the SCAN: frame coverage, length filter,
require_stop semantics, and reverse-strand coordinate mapping.
"""

from __future__ import annotations

from bioforge.tools.sequence.find_orfs import FindOrfsInput, find_orfs

# A 60-nt forward-strand ORF: ATG + 18 codons + TAA = 60 nt → 19-aa protein
_FWD_ORF_60NT = (
    "ATG"
    "GCG"
    "GCG"
    "GCG"
    "GCG"
    "GCG"  # 5 codons
    "GCG"
    "GCG"
    "GCG"
    "GCG"
    "GCG"  # 5 codons
    "GCG"
    "GCG"
    "GCG"
    "GCG"
    "GCG"  # 5 codons
    "GCG"
    "GCG"
    "GCG"  # 3 codons → 19 total
    "TAA"
)


async def test_finds_forward_strand_orf() -> None:
    out = await find_orfs(FindOrfsInput(sequence=_FWD_ORF_60NT, min_length_aa=10))
    assert out.num_orfs >= 1
    fwd_orfs = [o for o in out.orfs if o.strand == "+"]
    assert any(o.length_aa == 19 and o.protein.startswith("M") for o in fwd_orfs)


async def test_min_length_filter_excludes_short_orfs() -> None:
    """The 19-aa ORF must be excluded when min_length_aa > 19."""
    out = await find_orfs(FindOrfsInput(sequence=_FWD_ORF_60NT, min_length_aa=30))
    long_orfs = [o for o in out.orfs if o.length_aa >= 30]
    assert long_orfs == []


async def test_reverse_strand_orf_coordinates_map_back() -> None:
    """An ORF placed on the reverse strand should be reported with `strand='-'` and
    forward-strand coordinates that span the right region."""
    from Bio.Seq import Seq

    # Build a forward sequence whose rev-comp contains _FWD_ORF_60NT.
    upstream = "AAAA" * 5  # 20 nt of filler
    fwd_seq = str(Seq(upstream + _FWD_ORF_60NT).reverse_complement())

    out = await find_orfs(FindOrfsInput(sequence=fwd_seq, min_length_aa=15))
    rev_orfs = [o for o in out.orfs if o.strand == "-"]
    assert rev_orfs, f"Expected at least one reverse-strand ORF, got {out.orfs}"
    rev = rev_orfs[0]
    # The ORF spans the LAST 60 nt of the original sequence (because we rev-comp'd).
    assert rev.length_aa == 19
    assert 0 <= rev.dna_start < rev.dna_end <= len(fwd_seq)


async def test_restricting_frames_skips_others() -> None:
    out = await find_orfs(FindOrfsInput(sequence=_FWD_ORF_60NT, min_length_aa=10, frames=[1]))
    assert all(o.frame == 1 for o in out.orfs)


async def test_require_stop_excludes_unterminated() -> None:
    # 24 nt = ATG + 7 codons, NO stop. require_stop=True → excluded, require_stop=False → kept
    no_stop = "ATG" + "GCG" * 7
    out_strict = await find_orfs(FindOrfsInput(sequence=no_stop, min_length_aa=5, require_stop=True))
    assert out_strict.num_orfs == 0
    out_loose = await find_orfs(FindOrfsInput(sequence=no_stop, min_length_aa=5, require_stop=False))
    assert out_loose.num_orfs >= 1
    assert not out_loose.orfs[0].has_stop


async def test_orfs_sorted_longest_first() -> None:
    # Two ORFs of different lengths. find_orfs reports protein length INCLUDING the
    # initial Met but excluding the stop, so:
    #   ATG + 5×GCG + TAA  → "M" + 5×"A"  = 6 aa
    #   ATG + 20×GCG + TAA → "M" + 20×"A" = 21 aa
    short_orf = "ATG" + "GCG" * 5 + "TAA"
    long_orf = "ATG" + "GCG" * 20 + "TAA"
    spacer = "AAAA"
    seq = short_orf + spacer + long_orf
    out = await find_orfs(FindOrfsInput(sequence=seq, min_length_aa=3))
    aa_lengths = [o.length_aa for o in out.orfs]
    assert aa_lengths == sorted(aa_lengths, reverse=True)
    assert out.longest_orf_length_aa == 21


async def test_max_orfs_caps_result() -> None:
    # Several ORFs in different frames.
    repeated = ("ATG" + "GCG" * 20 + "TAA" + "AAAA") * 5
    out = await find_orfs(FindOrfsInput(sequence=repeated, min_length_aa=10, max_orfs=2))
    assert len(out.orfs) <= 2


async def test_is_registered() -> None:
    from bioforge.tools.registry import get_tool

    spec = get_tool("find_orfs")
    assert spec.cost_hint == "cheap"
    assert "annotation" in spec.tags
