"""Tests for build_phylogenetic_tree — pure-Python NJ/UPGMA tree building.

No network, no subprocess, no filesystem — Biopython's DistanceCalculator /
DistanceTreeConstructor run entirely in-process, so these tests exercise the
real code path throughout (nothing to mock).
"""

from __future__ import annotations

import pytest
from bioforge.tools import REGISTRY
from bioforge.tools.base import ToolError
from bioforge.tools.functional.phylogenetics import (
    AlignedSequenceIn,
    BuildPhylogeneticTreeInput,
    PhyloSequence,
    TreeMethod,
    _looks_like_dna,
    _parse_fasta,
    build_phylogenetic_tree,
)
from pydantic import ValidationError


# --- Registry --------------------------------------------------------------------


def test_build_phylogenetic_tree_registered():
    assert "build_phylogenetic_tree" in REGISTRY


def test_build_phylogenetic_tree_metadata():
    spec = REGISTRY["build_phylogenetic_tree"]
    assert spec.name == "build_phylogenetic_tree"
    assert spec.description
    assert spec.version
    assert spec.citations
    assert "functional" in spec.tags
    assert "phylogenetics" in spec.tags


# --- Input source validation: exactly one of sequences/aligned_fasta/aligned ------


def test_no_source_provided_rejected():
    with pytest.raises(ValidationError, match="exactly one"):
        BuildPhylogeneticTreeInput()


def test_two_sources_provided_rejected():
    with pytest.raises(ValidationError, match="exactly one"):
        BuildPhylogeneticTreeInput(
            sequences=[PhyloSequence(id="a", sequence="ATGC")],
            aligned_fasta=">a\nATGC\n>b\nATGA\n",
        )


def test_all_three_sources_rejected():
    with pytest.raises(ValidationError, match="exactly one"):
        BuildPhylogeneticTreeInput(
            sequences=[PhyloSequence(id="a", sequence="ATGC")],
            aligned_fasta=">a\nATGC\n",
            aligned=[AlignedSequenceIn(id="a", aligned_sequence="ATGC")],
        )


def test_sequences_only_is_valid():
    inp = BuildPhylogeneticTreeInput(sequences=[PhyloSequence(id="a", sequence="ATGC")])
    assert inp.sequences is not None


# --- id validation (whitespace / '>' guard, mirrors align_msa's MsaSequence) -------


def test_id_with_whitespace_rejected():
    with pytest.raises(ValidationError, match="whitespace"):
        PhyloSequence(id="seq A", sequence="ATGC")


def test_id_with_gt_rejected():
    with pytest.raises(ValidationError):
        PhyloSequence(id="seq>A", sequence="ATGC")


def test_empty_id_rejected():
    with pytest.raises(ValidationError):
        PhyloSequence(id="   ", sequence="ATGC")


def test_id_with_colon_and_comma_allowed():
    """Newick-reserved chars are fine — they get quote-escaped in the output, not truncated."""
    s = PhyloSequence(id="seqB:test", sequence="ATGC")
    assert s.id == "seqB:test"


# --- _parse_fasta ------------------------------------------------------------------


def test_parse_fasta_basic():
    text = ">seqA\nATGC\n>seqB\nATGA\n"
    assert _parse_fasta(text) == [("seqA", "ATGC"), ("seqB", "ATGA")]


def test_parse_fasta_multiline_sequence():
    text = ">seqA\nATGC\nATGC\n"
    assert _parse_fasta(text) == [("seqA", "ATGCATGC")]


def test_parse_fasta_empty_string():
    assert _parse_fasta("") == []


def test_parse_fasta_header_takes_first_token_only():
    text = ">seqA description here\nATGC\n"
    assert _parse_fasta(text) == [("seqA", "ATGC")]


# --- _looks_like_dna -----------------------------------------------------------------


def test_looks_like_dna_true_for_acgt():
    assert _looks_like_dna([("a", "ATGCATGC"), ("b", "ATGCATGA")]) is True


def test_looks_like_dna_false_for_protein():
    assert _looks_like_dna([("a", "MKVLAT"), ("b", "MKVLAS")]) is False


def test_looks_like_dna_ignores_gap_chars():
    assert _looks_like_dna([("a", "ATG-C"), ("b", "AT--C")]) is True


def test_looks_like_dna_empty_records_false():
    assert _looks_like_dna([]) is False


# --- Happy path: sequences input, identity model, NJ ------------------------------


def _aligned_dna() -> list[PhyloSequence]:
    return [
        PhyloSequence(id="seqA", sequence="ATGCATGCATGC"),
        PhyloSequence(id="seqB", sequence="ATGCATGCATGA"),
        PhyloSequence(id="seqC", sequence="ATGCATGGATGC"),
        PhyloSequence(id="seqD", sequence="TTGCATGCATGC"),
    ]


async def test_happy_path_sequences_identity_nj():
    out = await build_phylogenetic_tree(BuildPhylogeneticTreeInput(sequences=_aligned_dna()))
    assert out.tree_method == "nj"
    assert out.model == "identity"
    assert out.num_sequences == 4
    assert out.alignment_length == 12
    assert out.newick_string.endswith(";")
    assert set(out.leaf_names) == {"seqA", "seqB", "seqC", "seqD"}
    assert "seqA" in out.ascii_tree
    assert set(out.distance_matrix.keys()) == {"seqA", "seqB", "seqC", "seqD"}
    for name in out.leaf_names:
        assert out.distance_matrix[name][name] == 0.0


async def test_distance_matrix_is_symmetric():
    out = await build_phylogenetic_tree(BuildPhylogeneticTreeInput(sequences=_aligned_dna()))
    dm = out.distance_matrix
    for a in dm:
        for b in dm[a]:
            assert dm[a][b] == pytest.approx(dm[b][a])


# --- Happy path: aligned_fasta input -----------------------------------------------


async def test_happy_path_aligned_fasta():
    fasta = ">seqA\nATGCATGCATGC\n>seqB\nATGCATGCATGA\n>seqC\nATGCATGGATGC\n"
    out = await build_phylogenetic_tree(BuildPhylogeneticTreeInput(aligned_fasta=fasta))
    assert out.num_sequences == 3
    assert set(out.leaf_names) == {"seqA", "seqB", "seqC"}


async def test_aligned_fasta_no_headers_raises():
    """A non-empty string with no '>' headers parses to zero records — distinct from
    aligned_fasta="" (which the model_validator treats as 'not provided' since it's falsy)."""
    with pytest.raises(ToolError, match="no sequences"):
        await build_phylogenetic_tree(BuildPhylogeneticTreeInput(aligned_fasta="\nATGC\n"))


async def test_aligned_fasta_bare_header_raises():
    with pytest.raises(ToolError, match="empty header"):
        await build_phylogenetic_tree(BuildPhylogeneticTreeInput(aligned_fasta=">\nATGC\n>b\nATGA\n"))


# --- Happy path: aligned (align_msa passthrough) input -----------------------------


async def test_happy_path_aligned_passthrough():
    aligned = [
        AlignedSequenceIn(id="seqA", aligned_sequence="ATG-CATGCATGC"),
        AlignedSequenceIn(id="seqB", aligned_sequence="ATG-CATGCATGA"),
        AlignedSequenceIn(id="seqC", aligned_sequence="ATGACATGGATGC"),
    ]
    out = await build_phylogenetic_tree(BuildPhylogeneticTreeInput(aligned=aligned))
    assert out.num_sequences == 3
    assert out.alignment_length == 13


# --- Validation errors: duplicate ids, length mismatch, too few sequences ---------


async def test_duplicate_ids_raises():
    seqs = [
        PhyloSequence(id="seqA", sequence="ATGC"),
        PhyloSequence(id="seqA", sequence="ATGA"),
    ]
    with pytest.raises(ToolError, match="Duplicate sequence ids"):
        await build_phylogenetic_tree(BuildPhylogeneticTreeInput(sequences=seqs))


async def test_single_sequence_raises():
    seqs = [PhyloSequence(id="seqA", sequence="ATGC")]
    with pytest.raises(ToolError, match="At least 2 sequences"):
        await build_phylogenetic_tree(BuildPhylogeneticTreeInput(sequences=seqs))


async def test_unequal_length_raises_with_align_msa_hint():
    seqs = [
        PhyloSequence(id="seqA", sequence="ATGCATGC"),
        PhyloSequence(id="seqB", sequence="ATGCAT"),  # shorter, unaligned
    ]
    with pytest.raises(ToolError, match="align_msa"):
        await build_phylogenetic_tree(BuildPhylogeneticTreeInput(sequences=seqs))


# --- Two-sequence edge case: works, but flagged as trivial -------------------------


async def test_two_sequences_works_with_trivial_topology_caveat():
    seqs = [
        PhyloSequence(id="seqA", sequence="ATGCATGCATGC"),
        PhyloSequence(id="seqB", sequence="ATGCATGCATGA"),
    ]
    out = await build_phylogenetic_tree(BuildPhylogeneticTreeInput(sequences=seqs))
    assert out.num_sequences == 2
    assert any("no internal branching topology" in c for c in out.caveats)


# --- Silent-corruption guard: model/alphabet mismatch ------------------------------


async def test_protein_model_on_dna_input_rejected():
    seqs = _aligned_dna()
    with pytest.raises(ToolError, match="DNA/RNA"):
        await build_phylogenetic_tree(BuildPhylogeneticTreeInput(sequences=seqs, model="blosum62"))


async def test_dna_model_on_protein_input_rejected():
    seqs = [
        PhyloSequence(id="seqA", sequence="MKVLATWQR"),
        PhyloSequence(id="seqB", sequence="MKVLASWQR"),
    ]
    with pytest.raises(ToolError, match="protein"):
        await build_phylogenetic_tree(BuildPhylogeneticTreeInput(sequences=seqs, model="blastn"))


async def test_identity_model_works_for_both_alphabets():
    dna_out = await build_phylogenetic_tree(BuildPhylogeneticTreeInput(sequences=_aligned_dna(), model="identity"))
    assert dna_out.model == "identity"

    protein_seqs = [
        PhyloSequence(id="seqA", sequence="MKVLATWQR"),
        PhyloSequence(id="seqB", sequence="MKVLASWQR"),
    ]
    protein_out = await build_phylogenetic_tree(BuildPhylogeneticTreeInput(sequences=protein_seqs, model="identity"))
    assert protein_out.model == "identity"


async def test_correctly_matched_protein_model_works():
    seqs = [
        PhyloSequence(id="seqA", sequence="MKVLATWQR"),
        PhyloSequence(id="seqB", sequence="MKVLASWQR"),
        PhyloSequence(id="seqC", sequence="MKVLATWQS"),
    ]
    out = await build_phylogenetic_tree(BuildPhylogeneticTreeInput(sequences=seqs, model="blosum62"))
    assert out.model == "blosum62"


def test_invalid_model_name_raises():
    from bioforge.tools.functional.phylogenetics import _build_tree_sync

    with pytest.raises(ToolError, match="Unknown model"):
        _build_tree_sync([("a", "ATGC"), ("b", "ATGA")], "not_a_real_model", "nj")


async def test_invalid_model_name_via_public_tool_function():
    with pytest.raises(ToolError, match="Unknown model"):
        await build_phylogenetic_tree(BuildPhylogeneticTreeInput(sequences=_aligned_dna(), model="not_a_real_model"))


# --- UPGMA method ------------------------------------------------------------------


async def test_upgma_method_works_and_adds_clock_caveat():
    out = await build_phylogenetic_tree(BuildPhylogeneticTreeInput(sequences=_aligned_dna(), tree_method="upgma"))
    assert out.tree_method == "upgma"
    assert any("molecular clock" in c for c in out.caveats)


def test_tree_method_enum_values():
    assert TreeMethod.nj.value == "nj"
    assert TreeMethod.upgma.value == "upgma"


# --- Newick round-trip / special characters in ids ---------------------------------


async def test_newick_string_parses_back_with_biopython():
    from io import StringIO

    from Bio import Phylo

    out = await build_phylogenetic_tree(BuildPhylogeneticTreeInput(sequences=_aligned_dna()))
    tree = Phylo.read(StringIO(out.newick_string), "newick")
    parsed_names = {c.name for c in tree.get_terminals()}
    assert parsed_names == set(out.leaf_names)


async def test_newick_preserves_special_characters_in_ids():
    seqs = [
        PhyloSequence(id="seqB:test", sequence="ATGCATGCATGC"),
        PhyloSequence(id="seqC,x", sequence="ATGCATGCATGA"),
        PhyloSequence(id="plainC", sequence="ATGCATGGATGC"),
    ]
    out = await build_phylogenetic_tree(BuildPhylogeneticTreeInput(sequences=seqs))
    assert set(out.leaf_names) == {"seqB:test", "seqC,x", "plainC"}


# --- Tool-level metadata stamping ------------------------------------------------


async def test_tool_stamped_via_registry_execute():
    from bioforge.tools.registry import execute_tool

    result = await execute_tool(
        "build_phylogenetic_tree",
        {"sequences": [{"id": "a", "sequence": "ATGCATGC"}, {"id": "b", "sequence": "ATGCATGA"}]},
    )
    assert result.tool_name == "build_phylogenetic_tree"
    assert result.tool_version == "1.0.0"
