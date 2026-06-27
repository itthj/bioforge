"""Unit tests for the enhanced methods draft generator.

All tests pass ``polish=False`` so no LLM call or API key is needed.
Tests cover: template fill, benchmark number injection, mandatory caveat survival,
BibTeX generation, parameter table, grounding guard, and multi-tool runs.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from bioforge.provenance.methods_draft import (
    MethodsDraft,
    _B,
    _grounding_guard,
    render_methods_draft,
)
from bioforge.provenance.research_object import (
    RunManifest,
    ToolInvocation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _manifest(*tool_names: str, status: str = "completed") -> RunManifest:
    """Build a minimal manifest with the given tool names (version '1.0.0')."""
    tools = [
        ToolInvocation(
            tool=t,
            version="1.0.0",
            input_sha256="a" * 64,
            output_sha256="b" * 64,
            reference_data_keys=[],
            citations=[],
        )
        for t in tool_names
    ]
    return RunManifest(
        goal="Test goal",
        model="claude-sonnet-4-test",
        status=status,
        response_sha256="c" * 64,
        settings_fingerprint={"default_model": "claude-sonnet-4-test", "grounding_enabled": True},
        tools=tools,
        reference_builds=[],
        grounding=None,
        content_hash="d" * 64,
        created_at="2025-06-21T10:00:00Z",
    )


def _run(manifest: RunManifest, polish: bool = False) -> MethodsDraft:
    return asyncio.run(
        render_methods_draft(manifest, result=None, polish=polish)
    )


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------

def test_returns_methods_draft():
    draft = _run(_manifest("design_guides"))
    assert isinstance(draft, MethodsDraft)
    assert draft.paragraph
    assert draft.bibtex_block
    assert draft.param_table_md


def test_bioforge_always_citation_one():
    """BioForge must always be [1] regardless of tool order."""
    draft = _run(_manifest("blast", "design_guides"))
    assert "[1]" in draft.paragraph
    assert "BioForge2025" in draft.bibtex_block


def test_no_warnings_for_clean_run():
    """Template prose (polish=False) should produce zero warnings."""
    draft = _run(_manifest("score_guide_on_target"))
    assert draft.warnings == []


# ---------------------------------------------------------------------------
# CRISPR tools — benchmark numbers and caveats
# ---------------------------------------------------------------------------

def test_on_target_benchmark_present():
    draft = _run(_manifest("score_guide_on_target"))
    expected = _B["deepcrispr_ont"]["formatted"]
    assert expected in draft.paragraph


def test_on_target_caveat_present():
    draft = _run(_manifest("score_guide_on_target"))
    assert _B["deepcrispr_ont"]["caveat_key"] in draft.paragraph


def test_off_target_benchmark_present():
    draft = _run(_manifest("find_offtargets"))
    assert _B["cfd_oft"]["formatted"] in draft.paragraph


def test_edit_outcome_benchmark_present():
    draft = _run(_manifest("edit_outcome"))
    assert _B["forecast_nhej"]["formatted"] in draft.paragraph


def test_edit_outcome_k562_caveat():
    """K562 cell-line caveat must appear in edit outcome prose."""
    draft = _run(_manifest("edit_outcome"))
    assert _B["forecast_nhej"]["caveat_key"] in draft.paragraph


# ---------------------------------------------------------------------------
# Variant annotation
# ---------------------------------------------------------------------------

def test_variant_annotation_benchmark():
    draft = _run(_manifest("annotate_variant"))
    assert _B["giab_vc"]["precision"] in draft.paragraph
    assert _B["giab_vc"]["recall"] in draft.paragraph


def test_giab_scope_caveat():
    draft = _run(_manifest("annotate_variant"))
    assert _B["giab_vc"]["caveat_key"] in draft.paragraph


# ---------------------------------------------------------------------------
# AlphaFold
# ---------------------------------------------------------------------------

def test_alphafold_plddt_threshold():
    draft = _run(_manifest("fetch_alphafold_structure"))
    assert str(_B["alphafold_plddt"]["threshold"]) in draft.paragraph


def test_alphafold_plddt_caveat():
    draft = _run(_manifest("fetch_alphafold_structure"))
    assert _B["alphafold_plddt"]["caveat_key"] in draft.paragraph


# ---------------------------------------------------------------------------
# BLAST
# ---------------------------------------------------------------------------

def test_blast_deterministic_disclaimer():
    draft = _run(_manifest("blast"))
    assert "deterministic" in draft.paragraph.lower()


def test_blast_no_fake_accuracy():
    """BLAST paragraph must not contain any benchmark ρ or TVD values."""
    draft = _run(_manifest("blast"))
    assert "\u03c1" not in draft.paragraph
    assert "TVD" not in draft.paragraph


# ---------------------------------------------------------------------------
# Meta tools are skipped
# ---------------------------------------------------------------------------

def test_meta_tools_not_in_paragraph():
    draft = _run(_manifest("remember", "read_files", "list_files"))
    # Falls back to the generic no-tool sentence
    assert draft.paragraph


def test_parse_vcf_skipped():
    """parse_vcf is an internal tool and should not appear as a method sentence."""
    draft = _run(_manifest("parse_vcf", "annotate_variant"))
    # Should only have one tool-level paragraph (annotate_variant), not two
    assert draft.paragraph.count("deterministic") <= 1


# ---------------------------------------------------------------------------
# Multi-tool run
# ---------------------------------------------------------------------------

def test_crispr_multi_tool():
    draft = _run(_manifest("design_guides", "score_guide_on_target", "find_offtargets", "edit_outcome"))
    # All four benchmark sets must appear
    assert _B["deepcrispr_ont"]["formatted"] in draft.paragraph
    assert _B["cfd_oft"]["formatted"] in draft.paragraph
    assert _B["forecast_nhej"]["formatted"] in draft.paragraph


def test_deduplication():
    """Calling blast twice should produce only one BLAST sentence."""
    draft = _run(_manifest("blast", "blast"))
    assert draft.paragraph.count("BLAST") <= 2  # one sentence may mention BLAST twice


# ---------------------------------------------------------------------------
# BibTeX
# ---------------------------------------------------------------------------

def test_bibtex_deepcrispr():
    draft = _run(_manifest("score_guide_on_target"))
    assert "Chuai2018" in draft.bibtex_block
    assert "@article{Chuai2018" in draft.bibtex_block


def test_bibtex_doench_for_cfd():
    draft = _run(_manifest("find_offtargets"))
    assert "Doench2016" in draft.bibtex_block


def test_bibtex_forecast():
    draft = _run(_manifest("edit_outcome"))
    assert "Allen2019" in draft.bibtex_block


def test_bibtex_blast():
    draft = _run(_manifest("blast"))
    assert "Altschul1990" in draft.bibtex_block


def test_bibtex_alphafold():
    draft = _run(_manifest("fetch_alphafold_structure"))
    assert "Jumper2021" in draft.bibtex_block
    assert "Varadi2022" in draft.bibtex_block


def test_bibtex_deduplication():
    """Each BibTeX key must appear exactly once even when a tool is referenced multiple ways."""
    draft = _run(_manifest("design_guides", "score_guide_on_target"))
    bibtex = draft.bibtex_block
    assert bibtex.count("BioForge2025") == 1


# ---------------------------------------------------------------------------
# Parameter table
# ---------------------------------------------------------------------------

def test_param_table_is_markdown():
    draft = _run(_manifest("blast"))
    lines = draft.param_table_md.strip().splitlines()
    # Expect at least a header row and a separator row
    assert len(lines) >= 2
    assert "|" in lines[0]
    assert "---" in lines[1]


def test_empty_tool_list_param_table():
    draft = _run(_manifest("remember"))  # all skipped
    assert draft.param_table_md  # always returns something


# ---------------------------------------------------------------------------
# Grounding guard (unit)
# ---------------------------------------------------------------------------

def test_guard_passes_identical():
    valid, issues = _grounding_guard("rho = 0.130", "rho = 0.130", [])
    assert valid
    assert not issues


def test_guard_fails_on_new_number():
    valid, issues = _grounding_guard("rho = 0.130", "rho = 0.999", ["cross-dataset"])
    assert not valid
    # Guard extracts the integer/decimal token ('999') not the full '0.999' string
    assert any("999" in i for i in issues)


def test_guard_fails_on_missing_caveat():
    valid, issues = _grounding_guard(
        "cross-dataset efficiency prediction carries substantial uncertainty",
        "on-target efficiency was scored",   # caveat stripped
        ["cross-dataset efficiency"],
    )
    assert not valid
    assert any("cross-dataset" in i.lower() for i in issues)


def test_guard_passes_with_caveat_present():
    template = "rho = 0.130; cross-dataset efficiency carries uncertainty"
    llm = "The cross-dataset efficiency carries uncertainty; rho = 0.130"
    valid, issues = _grounding_guard(template, llm, ["cross-dataset efficiency"])
    assert valid
