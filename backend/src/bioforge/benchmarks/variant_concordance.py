"""§13 GIAB variant-calling concordance metric -- precision / recall / F1 vs a truth set.

This is the SCORING half of the Genome-in-a-Bottle benchmark: given a set of CALLED variants, a
TRUTH set (e.g. GIAB HG002), and the truth set's HIGH-CONFIDENCE regions, compute precision,
recall and F1 -- stratified into SNV / INDEL / ALL, and restricted to the confident regions (the
GIAB stratification that makes the numbers meaningful).

What it is honest about (rule 18, §0):
  * **Restricted to high-confidence regions.** Variants outside the confident BED are excluded
    from BOTH the truth and called sets before scoring -- exactly how GIAB-style evaluation works,
    so a caller is not penalised for regions GIAB itself does not assert.
  * **Genotype-agnostic, exact normalized-allele match.** Each variant is reduced to a
    parsimoniously-trimmed (chrom, pos, ref, alt) key; matching is set intersection over those
    keys. This is NOT the haplotype-aware comparison hap.py / vcfeval perform, and the trim is
    not reference-based left-alignment -- so some indel-representation differences will be
    undercounted. The result carries that caveat verbatim. For a release-grade GIAB number, run
    hap.py; this is the in-platform metric + the gate logic a future variant-calling path feeds.

What it is NOT: a variant CALLER. The platform's variant tools are annotation-only; no caller is
integrated, so the end-to-end GIAB benchmark stays `not_yet_wired` in the Accuracy Report. This
module is the metric that becomes live the moment a (digest-pinned) caller + the GIAB truth-set
download exist.

Pure stdlib + pydantic -- no pysam/cyvcf2 (consistent with parse_vcf's deliberate choice).
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

VariantClass = Literal["SNV", "INDEL", "ALL"]

_CONCORDANCE_CAVEAT = (
    "Concordance is genotype-agnostic, exact normalized-allele matching (parsimonious trim, NOT "
    "reference-based left-alignment), restricted to the high-confidence regions. It is NOT the "
    "haplotype-aware comparison hap.py / vcfeval perform, so some indel-representation differences "
    "may be undercounted. For a release-grade GIAB number, run hap.py; this is the in-platform "
    "approximation and the gate logic."
)


@dataclass(frozen=True)
class VariantCall:
    """A single biallelic variant. `pos` is 1-based (VCF convention)."""

    chrom: str
    pos: int
    ref: str
    alt: str

    @property
    def is_snv(self) -> bool:
        return len(self.ref) == 1 and len(self.alt) == 1

    @property
    def start0(self) -> int:
        """0-based start position (for BED region membership)."""
        return self.pos - 1

    def normalized_key(self) -> tuple[str, int, str, str]:
        """Parsimonious (chrom, pos, ref, alt) key: trim shared suffix then shared prefix bases.

        Reference-free normalization -- it reconciles the common 'same indel, different padding'
        representations for exact set matching, but does NOT left-align across a homopolymer (that
        needs the reference). SNVs are returned unchanged.
        """
        ref, alt, pos = self.ref.upper(), self.alt.upper(), self.pos
        while len(ref) > 1 and len(alt) > 1 and ref[-1] == alt[-1]:
            ref, alt = ref[:-1], alt[:-1]
        while len(ref) > 1 and len(alt) > 1 and ref[0] == alt[0]:
            ref, alt, pos = ref[1:], alt[1:], pos + 1
        return (self.chrom, pos, ref, alt)


@dataclass(frozen=True)
class ConfidentRegion:
    """A high-confidence interval. `start` is 0-based, `end` exclusive (BED half-open)."""

    chrom: str
    start: int
    end: int


class ConcordanceMetrics(BaseModel):
    """precision / recall / F1 for one variant class within the confident regions."""

    variant_class: VariantClass
    tp: int = Field(description="Called variants that match a truth variant (in confident regions).")
    fp: int = Field(description="Called variants with no truth match (in confident regions).")
    fn: int = Field(description="Truth variants the caller missed (in confident regions).")
    precision: float
    recall: float
    f1: float


class VariantConcordanceResult(BaseModel):
    """GIAB-style concordance of a called set vs a truth set, stratified + honestly caveated."""

    by_class: list[ConcordanceMetrics]
    n_truth_total: int
    n_called_total: int
    n_truth_in_regions: int = Field(description="Truth variants inside the confident regions (the scored denominator).")
    n_called_in_regions: int
    caveat: str


def _build_region_index(regions: Iterable[ConfidentRegion]) -> dict[str, tuple[list[int], list[int]]]:
    """Per-chrom sorted (starts, ends) for O(log n) membership. Assumes non-overlapping BED
    intervals (the GIAB high-confidence BED is non-overlapping); adjacent intervals are fine."""
    by_chrom: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for r in regions:
        if r.end <= r.start:
            raise ValueError(f"ConfidentRegion {r} has end <= start.")
        by_chrom[r.chrom].append((r.start, r.end))
    index: dict[str, tuple[list[int], list[int]]] = {}
    for chrom, ivals in by_chrom.items():
        ivals.sort()
        index[chrom] = ([s for s, _ in ivals], [e for _, e in ivals])
    return index


def _in_regions(index: dict[str, tuple[list[int], list[int]]], chrom: str, pos0: int) -> bool:
    entry = index.get(chrom)
    if entry is None:
        return False
    starts, ends = entry
    i = bisect.bisect_right(starts, pos0) - 1  # rightmost interval whose start <= pos0
    return i >= 0 and pos0 < ends[i]


def _safe_div(num: int, denom: int) -> float:
    """num/denom, with 0/0 and x/0 -> 0.0 (documented convention; JSON-safe, no NaN)."""
    return num / denom if denom > 0 else 0.0


def _metrics_for(variant_class: VariantClass, called_keys: set, truth_keys: set) -> ConcordanceMetrics:
    tp = len(called_keys & truth_keys)
    fp = len(called_keys - truth_keys)
    fn = len(truth_keys - called_keys)
    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    f1 = _safe_div(2 * tp, 2 * tp + fp + fn)  # = 2PR/(P+R), guarded
    return ConcordanceMetrics(
        variant_class=variant_class,
        tp=tp,
        fp=fp,
        fn=fn,
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
    )


def score_variant_concordance(
    called: Iterable[VariantCall],
    truth: Iterable[VariantCall],
    regions: Iterable[ConfidentRegion],
) -> VariantConcordanceResult:
    """Compute stratified precision / recall / F1 of `called` vs `truth` within `regions`.

    Both sets are filtered to the confident regions, reduced to parsimonious normalized keys, and
    split into SNV / INDEL. Matching is set intersection over the keys (genotype-agnostic). Returns
    per-class metrics plus the honesty caveat.
    """
    called = list(called)
    truth = list(truth)
    index = _build_region_index(regions)

    def _keys(variants: list[VariantCall]) -> tuple[set, set, int]:
        snv: set = set()
        indel: set = set()
        in_region = 0
        for v in variants:
            if not _in_regions(index, v.chrom, v.start0):
                continue
            in_region += 1
            (snv if v.is_snv else indel).add(v.normalized_key())
        return snv, indel, in_region

    called_snv, called_indel, n_called_in = _keys(called)
    truth_snv, truth_indel, n_truth_in = _keys(truth)

    by_class = [
        _metrics_for("SNV", called_snv, truth_snv),
        _metrics_for("INDEL", called_indel, truth_indel),
        _metrics_for("ALL", called_snv | called_indel, truth_snv | truth_indel),
    ]
    return VariantConcordanceResult(
        by_class=by_class,
        n_truth_total=len(truth),
        n_called_total=len(called),
        n_truth_in_regions=n_truth_in,
        n_called_in_regions=n_called_in,
        caveat=_CONCORDANCE_CAVEAT,
    )


def variant_calls_from_parsed(parsed: Iterable[object]) -> list[VariantCall]:
    """Adapt `parse_vcf.Variant` records into `VariantCall`s, exploding multi-allelic sites.

    Each parsed record exposes `chrom`, `pos`, `ref`, `alt` (a list of ALT alleles). Symbolic
    ALTs (`<DEL>`, `<*>`, breakends) are skipped -- this metric scores precise sequence alleles.
    Accepts any object with those attributes (duck-typed) to avoid a hard import cycle.
    """
    calls: list[VariantCall] = []
    for rec in parsed:
        chrom = str(rec.chrom)
        pos = int(rec.pos)
        ref = str(rec.ref).upper()
        for alt in rec.alt:
            a = str(alt).upper()
            if not a or a.startswith("<") or "[" in a or "]" in a or a == "*":
                continue  # symbolic / breakend / spanning-deletion -- not a precise allele
            calls.append(VariantCall(chrom=chrom, pos=pos, ref=ref, alt=a))
    return calls
