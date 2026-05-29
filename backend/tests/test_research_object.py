"""§10 reproducibility research-object — deterministic, content-addressed run manifests.

Pure reads over a synthesized AgentResult (no agent run, no network). Asserts the lineage
is captured, the content hash is stable across builds yet sensitive to inputs, secrets are
never fingerprinted, and the export round-trips.
"""

from __future__ import annotations

import json
from pathlib import Path

import bioforge.tools  # noqa: F401 — ensure tools are registered for reference_data_keys lookups
from bioforge.agent.loop import AgentResult, AgentStep
from bioforge.config import settings
from bioforge.provenance import build_run_manifest, export_research_object

_GUIDE = "ACGTACGTACGTACGTACGT"  # 20 nt


def _tool_step(name: str, tool_input: dict, tool_output: dict) -> AgentStep:
    return AgentStep(
        idx=0, type="tool_call", duration_ms=5, tool_name=name, tool_input=tool_input, tool_output=tool_output
    )


def _validation_step() -> AgentStep:
    return AgentStep(
        idx=1,
        type="validation",
        duration_ms=2,
        verdict={
            "ok": True,
            "mode": "annotate",
            "enforced": False,
            "soundness": {"ok": True},
            "ood": {"ok": True},
        },
    )


def _result(guide: str = _GUIDE) -> AgentResult:
    steps = [
        _tool_step(
            "find_offtargets",
            {"guide": guide, "database": "nt"},
            {"tool_name": "find_offtargets", "tool_version": "1.0.0", "citations": ["Hsu PD 2013"], "hits": []},
        ),
        _validation_step(),
    ]
    return AgentResult(
        goal="find off-targets",
        project_id="p",
        response_text="No off-targets found.",
        steps=steps,
        model="claude-sonnet-4-6",
    )


def test_manifest_captures_tool_lineage() -> None:
    m = build_run_manifest(_result())
    assert len(m.tools) == 1
    inv = m.tools[0]
    assert inv.tool == "find_offtargets"
    assert inv.version == "1.0.0"
    assert inv.reference_data_keys == ["ncbi_blast"]
    assert "Hsu PD 2013" in inv.citations
    assert len(inv.input_sha256) == 64
    assert len(inv.output_sha256) == 64
    assert len(m.content_hash) == 64


def test_reference_build_for_live_service_is_unpinned() -> None:
    m = build_run_manifest(_result())
    blast = next(rb for rb in m.reference_builds if rb.key == "ncbi_blast")
    assert blast.pinned is False
    assert blast.pin is None


def test_reference_build_for_owned_weights_is_pinned() -> None:
    # score_guide_on_target declares reference_data_keys=["deepcrispr_weights"], which BioForge
    # version-pins via deepcrispr_upstream_commit.
    steps = [
        _tool_step(
            "score_guide_on_target",
            {"protospacer": "GAGTCCGAGCAGAAGAAGAA"},
            {"tool_name": "score_guide_on_target", "tool_version": "1.0.0", "citations": [], "on_target_score": 0.5},
        )
    ]
    result = AgentResult(goal="score", project_id="p", response_text="x", steps=steps, model="m")
    m = build_run_manifest(result)
    weights = next(rb for rb in m.reference_builds if rb.key == "deepcrispr_weights")
    assert weights.pinned is True
    assert weights.pin == settings.deepcrispr_upstream_commit


def test_content_hash_is_stable_across_builds() -> None:
    a = build_run_manifest(_result())
    b = build_run_manifest(_result())
    # created_at differs build-to-build; the content hash must not.
    assert a.content_hash == b.content_hash


def test_content_hash_is_sensitive_to_inputs() -> None:
    baseline = build_run_manifest(_result(_GUIDE))
    changed = build_run_manifest(_result("TTTTACGTACGTACGTACGT"))
    assert baseline.content_hash != changed.content_hash


def test_settings_fingerprint_excludes_secrets() -> None:
    fp = build_run_manifest(_result()).settings_fingerprint
    assert "default_model" in fp
    assert "grounding_mode" in fp
    for secret in ("anthropic_api_key", "db_url", "entrez_email"):
        assert secret not in fp


def test_grounding_summary_is_recorded() -> None:
    m = build_run_manifest(_result())
    assert m.grounding == {
        "ok": True,
        "mode": "annotate",
        "enforced": False,
        "soundness_ok": True,
        "ood_ok": True,
    }


def test_grounding_summary_none_without_validation_step() -> None:
    steps = [
        _tool_step(
            "find_offtargets",
            {"guide": _GUIDE},
            {"tool_name": "find_offtargets", "tool_version": "1.0.0", "citations": [], "hits": []},
        )
    ]
    result = AgentResult(goal="g", project_id="p", response_text="r", steps=steps, model="m")
    assert build_run_manifest(result).grounding is None


def test_export_round_trips(tmp_path: Path) -> None:
    m = build_run_manifest(_result())
    path = export_research_object(m, tmp_path)
    assert path.exists()
    assert m.content_hash[:12] in path.name
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["content_hash"] == m.content_hash
    assert loaded["tools"][0]["tool"] == "find_offtargets"
    assert loaded["schema_version"] == "bioforge-research-object/1"
