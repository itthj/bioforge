"""Tests for the GRCh38 off-target genomic-placement gate.

The gate's whole job is to be HONEST about which BLAST hits can be shown on hg38:
place GRCh38 primary chromosomes, refuse everything else (gene/transcript records,
scaffolds, the wrong build, non-human subjects). A wrong placement is worse than no
placement, so the refusals are the important cases.
"""

from __future__ import annotations

import json

from bioforge.tools.sequence.genomic_placement import (
    _ACCESSION_DATA_PATH,
    _accession_map,
    is_grch38_chromosome,
    resolve_genomic_placement,
)


def test_places_grch38_chromosome_plus_strand() -> None:
    p = resolve_genomic_placement("NC_000001.11", 1001, 1020)
    assert p is not None
    assert p.build == "GRCh38"
    assert p.chromosome == "chr1"
    # 1-based inclusive [1001, 1020] -> 0-based half-open [1000, 1020).
    assert (p.start, p.end) == (1000, 1020)
    assert p.strand == "+"
    assert p.source_accession == "NC_000001.11"


def test_minus_strand_is_normalized_but_recorded() -> None:
    # BLAST reports subject_start > subject_end for a minus-strand subject hit.
    p = resolve_genomic_placement("NC_000023.11", 5000, 4981)
    assert p is not None
    assert p.chromosome == "chrX"
    assert (p.start, p.end) == (4980, 5000)  # normalized so start < end
    assert p.strand == "-"


def test_mitochondrion_places_as_chrM() -> None:
    p = resolve_genomic_placement("NC_012920.1", 100, 119)
    assert p is not None and p.chromosome == "chrM"


def test_refuses_grch37_accession_wrong_build() -> None:
    # NC_000001.10 is GRCh37 chr1 -- its coordinates differ from GRCh38, so it must
    # NOT be placed on hg38.
    assert resolve_genomic_placement("NC_000001.10", 1001, 1020) is None
    assert is_grch38_chromosome("NC_000001.10") is False


def test_refuses_gene_transcript_and_scaffold_records() -> None:
    for acc in ("NG_007524.2", "NM_007294.4", "XM_017001234.1", "NT_187361.1", "NW_009646201.1"):
        assert resolve_genomic_placement(acc, 1001, 1020) is None


def test_refuses_non_human_and_empty_accession() -> None:
    assert resolve_genomic_placement("CP000819.1", 1001, 1020) is None  # E. coli
    assert resolve_genomic_placement("", 1001, 1020) is None


def test_refuses_degenerate_coordinates() -> None:
    # BLAST is 1-based; a 0 means the coordinate was missing -- never invent a locus.
    assert resolve_genomic_placement("NC_000001.11", 0, 0) is None
    assert resolve_genomic_placement("NC_000001.11", 0, 20) is None


def test_accession_map_matches_committed_data_and_is_complete() -> None:
    data = json.loads(_ACCESSION_DATA_PATH.read_text(encoding="utf-8"))
    mapping = _accession_map()
    assert mapping == data["accession_to_ucsc"]
    # 22 autosomes + X + Y + MT.
    assert len(mapping) == 25
    expected = {f"chr{i}" for i in range(1, 23)} | {"chrX", "chrY", "chrM"}
    assert set(mapping.values()) == expected
    # Every accession is a versioned RefSeq chromosome accession.
    assert all(a.startswith("NC_") and "." in a for a in mapping)


def test_data_file_carries_provenance() -> None:
    data = json.loads(_ACCESSION_DATA_PATH.read_text(encoding="utf-8"))
    prov = data["_provenance"]
    assert prov["build"] == "GRCh38"
    assert prov["source_url"].startswith("https://ftp.ncbi.nlm.nih.gov/")
    assert len(prov["source_sha256"]) == 64
