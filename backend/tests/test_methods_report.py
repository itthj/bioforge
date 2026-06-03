"""Unit tests for the Markdown methods/reproducibility report renderer.

Builds a RunManifest directly (no agent run needed), mirroring test_ro_crate.py, and asserts
the rendered Markdown carries the scientific-record sections, exact tool versions, deduplicated
references, honest reference-data provenance, and the grounding verdict.
"""

from __future__ import annotations

from bioforge.provenance import render_methods_report
from bioforge.provenance.research_object import (
    ReferenceBuild,
    RunManifest,
    ToolInvocation,
)


def _manifest(*, grounding: dict | None = None, status: str = "completed") -> RunManifest:
    return RunManifest(
        goal="Design Cas9 guides for the human EMX1 locus",
        model="claude-sonnet-4-test",
        status=status,
        response_sha256="a" * 64,
        settings_fingerprint={"default_model": "claude-sonnet-4-test", "grounding_enabled": True},
        tools=[
            ToolInvocation(
                tool="design_guides",
                version="1.2.0",
                input_sha256="b" * 64,
                output_sha256="c" * 64,
                reference_data_keys=[],
                citations=["Doench JG et al. (2016) Optimized sgRNA design. Nat Biotechnol 34:184-191"],
            ),
            ToolInvocation(
                tool="blast",
                version="2.0.0",
                input_sha256="d" * 64,
                output_sha256="e" * 64,
                reference_data_keys=["ncbi_blast"],
                citations=[
                    "Altschul SF et al. (1990) Basic local alignment search tool. J Mol Biol 215:403-410",
                    "Doench JG et al. (2016) Optimized sgRNA design. Nat Biotechnol 34:184-191",
                ],
            ),
        ],
        reference_builds=[ReferenceBuild(key="ncbi_blast", pin=None, pinned=False)],
        grounding=grounding,
        content_hash="f" * 64,
        created_at="2026-01-01T00:00:00+00:00",
    )


def test_report_has_core_sections() -> None:
    md = render_methods_report(_manifest())
    for heading in (
        "# BioForge computational methods record",
        "## Summary",
        "## Computational methods",
        "## Software and tools",
        "## Reference data and databases",
        "## Validation and grounding",
        "## Reproducibility and provenance",
        "## Data and code availability",
        "## References",
        "## Limitations",
    ):
        assert heading in md, f"missing section: {heading}"


def test_report_includes_tool_versions_and_hash() -> None:
    md = render_methods_report(_manifest())
    assert "design_guides" in md and "1.2.0" in md
    assert "blast" in md and "2.0.0" in md
    # Full content hash appears in the provenance section; short form in the header.
    assert "f" * 64 in md
    assert "f" * 12 in md


def test_references_are_deduplicated_and_numbered() -> None:
    md = render_methods_report(_manifest())
    # The Doench citation appears in two tools but must be listed exactly once.
    assert md.count("Doench JG et al. (2016) Optimized sgRNA design. Nat Biotechnol 34:184-191") == 1
    # Two distinct references, numbered 1 and 2.
    assert "1. " in md and "2. " in md


def test_live_reference_data_is_flagged_not_pinned() -> None:
    md = render_methods_report(_manifest())
    assert "not version-pinned" in md.lower()
    assert "ncbi_blast" in md


def test_grounding_absent_is_stated_honestly() -> None:
    md = render_methods_report(_manifest(grounding=None))
    assert "no grounding/validation verdict was recorded" in md.lower()


def test_grounding_present_reports_verdict() -> None:
    md = render_methods_report(
        _manifest(grounding={"ok": True, "mode": "enforce", "enforced": True, "soundness_ok": True})
    )
    assert "passed" in md.lower()
    assert "enforce" in md.lower()


def test_shadow_mode_grounding_noted_in_limitations() -> None:
    md = render_methods_report(_manifest(grounding={"ok": True, "mode": "shadow", "enforced": False}))
    assert "shadow mode" in md.lower()


def test_critique_failed_status_surfaced() -> None:
    md = render_methods_report(_manifest(status="critique_failed"))
    assert "provisional" in md.lower() or "did not" in md.lower()


def test_empty_tools_run_is_handled() -> None:
    m = _manifest()
    m.tools = []
    m.reference_builds = []
    md = render_methods_report(m)
    assert "no external" in md.lower()
    assert "## References" in md  # still renders all sections


def test_deterministic_for_same_manifest() -> None:
    a = render_methods_report(_manifest())
    b = render_methods_report(_manifest())
    assert a == b


def test_tables_are_contiguous_gfm() -> None:
    """GFM tables require header, separator, and rows on consecutive lines (no blank lines)."""
    md = render_methods_report(_manifest())
    lines = md.split("\n")
    sep_idxs = [i for i, ln in enumerate(lines) if set(ln.strip()) <= {"|", "-", " "} and "-" in ln and "|" in ln]
    assert sep_idxs, "expected at least one table separator row"
    for i in sep_idxs:
        # The line before a separator (header) and after (first data row) must be table rows.
        assert lines[i - 1].lstrip().startswith("|"), "table header not contiguous with separator"
        assert lines[i + 1].lstrip().startswith("|"), "table body not contiguous with separator"
