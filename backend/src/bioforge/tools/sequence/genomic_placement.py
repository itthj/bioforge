"""Place a BLAST off-target hit on the GRCh38 genome -- honestly, or not at all.

A `find_offtargets` hit carries an `accession` plus `subject_start`/`subject_end`. Those
coordinates are on whatever BLAST subject matched -- which may be a GRCh38 primary-assembly
chromosome (e.g. `NC_000001.11`), but may equally be a gene record (`NG_`), a transcript
(`NM_`/`XM_`), a scaffold, a different assembly/build, or a non-human organism entirely.

A genome browser pointed at hg38 may only show a hit whose accession IS a GRCh38 primary
chromosome -- anything else would be placed at a coordinate that means something different
(or nothing) on hg38. This module is the gate: it resolves a placement ONLY for the
committed, sourced GRCh38 chromosome accessions and returns `None` for everything else.
The accession VERSION matters: `NC_000001.10` is GRCh37 chr1, `NC_000001.11` is GRCh38 chr1
-- only the latter is placeable on hg38.

The accession -> UCSC-name map is sourced from the NCBI GRCh38 assembly report and committed
with provenance + sha256 (see `data/grch38_chromosome_accessions.json`), never from memory.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

_ACCESSION_DATA_PATH = Path(__file__).parent / "data" / "grch38_chromosome_accessions.json"

# UCSC-style names of the GRCh38 hosted IGV.js genome — the build a placement targets.
GENOME_BUILD = "GRCh38"
IGV_GENOME_ID = "hg38"


class GenomicPlacement(BaseModel):
    """A BLAST off-target hit resolved to a GRCh38 chromosome locus.

    Coordinates are 0-based half-open (BED / igv.js feature convention), normalized so
    `start < end` regardless of the BLAST subject orientation. `strand` records that
    orientation ('-' when BLAST reported subject_start > subject_end).
    """

    build: Literal["GRCh38"] = GENOME_BUILD
    chromosome: str = Field(description="UCSC-style contig name, e.g. 'chr1' / 'chrX' / 'chrM'.")
    start: int = Field(ge=0, description="0-based, inclusive.")
    end: int = Field(description="0-based, exclusive (end > start).")
    strand: Literal["+", "-"]
    source_accession: str = Field(description="The GRCh38 chromosome RefSeq accession that was placed.")


@lru_cache(maxsize=1)
def _accession_map() -> dict[str, str]:
    """Committed GRCh38 chromosome RefSeq accession -> UCSC-style name map.

    Sourced from the NCBI GRCh38 assembly report (assembled-molecule rows); provenance +
    sha256 live in the JSON header. Versioned accessions only — build-specific by design.
    """
    data = json.loads(_ACCESSION_DATA_PATH.read_text(encoding="utf-8"))
    return dict(data["accession_to_ucsc"])


def is_grch38_chromosome(accession: str) -> bool:
    """True iff `accession` is a recognized GRCh38 primary-assembly chromosome RefSeq."""
    return accession in _accession_map()


def _strip_version(accession: str) -> str:
    """The versionless RefSeq base, e.g. 'NC_000001.11' -> 'NC_000001'."""
    return accession.split(".", 1)[0]


@lru_cache(maxsize=1)
def _base_to_chromosome() -> dict[str, str]:
    """Versionless accession base -> UCSC name, derived from the committed GRCh38 map.

    Lets the refusal reason recognize a DIFFERENT-build version of a known chromosome
    (e.g. `NC_000001.10` is GRCh37 chr1) and say exactly that, instead of a generic
    'not a chromosome' message. Built from the same committed source -- no new data.
    """
    return {_strip_version(acc): ucsc for acc, ucsc in _accession_map().items()}


def placement_refusal_reason(accession: str) -> str | None:
    """Why a hit was NOT placed on hg38, or None if it IS a GRCh38 chromosome.

    Distinguishes the dangerous wrong-build case -- a chromosome accession from a different
    assembly version (same base, different version: GRCh37 `NC_000001.10` vs GRCh38
    `NC_000001.11`), whose coordinates differ between builds -- from a non-chromosome record
    (gene/transcript/scaffold/non-human). The gate already REFUSES both; this only makes the
    explanation specific (v4 section 6 / section 10: 'the patch level matters').
    """
    if accession in _accession_map():
        return None
    base = _strip_version(accession)
    chrom = _base_to_chromosome().get(base)
    if chrom is not None:
        grch38 = next(a for a in _accession_map() if _strip_version(a) == base)
        return (
            f"{accession} is {chrom} on a different assembly build (GRCh38 uses {grch38}); "
            "coordinates differ between builds, so it is not placed on hg38."
        )
    label = accession or "(no accession)"
    return (
        f"{label} is not a GRCh38 primary-assembly chromosome (e.g. a gene/transcript "
        "record, scaffold, or non-human subject); not placed on hg38."
    )


def resolve_genomic_placement(
    accession: str,
    subject_start: int,
    subject_end: int,
) -> GenomicPlacement | None:
    """Resolve a BLAST hit to a GRCh38 locus, or `None` if it cannot be soundly placed.

    Returns `None` (refuse to place) when:
      - the accession is not a recognized GRCh38 primary chromosome (gene/transcript record,
        scaffold, wrong build/version, or a non-human subject), or
      - the subject coordinates are non-positive / degenerate (BLAST is 1-based, so a 0 means
        "missing"; we never invent a position).

    On success the 1-based inclusive BLAST coordinates are normalized to 0-based half-open
    with start < end, and the strand records the original BLAST orientation.
    """
    ucsc = _accession_map().get(accession)
    if ucsc is None:
        return None
    lo = min(subject_start, subject_end)
    hi = max(subject_start, subject_end)
    # BLAST subject coordinates are 1-based; a 0 (or negative) means the value was missing.
    if lo < 1 or hi < lo:
        return None
    strand: Literal["+", "-"] = "-" if subject_start > subject_end else "+"
    return GenomicPlacement(
        chromosome=ucsc,
        start=lo - 1,
        end=hi,
        strand=strand,
        source_accession=accession,
    )
