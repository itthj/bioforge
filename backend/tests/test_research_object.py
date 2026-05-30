"""§10 reproducibility research-object — deterministic, content-addressed run manifests.

Pure reads over a synthesized AgentResult (no agent run, no network). Asserts the lineage
is captured, the content hash is stable across builds yet sensitive to inputs, secrets are
never fingerprinted, and the export round-trips.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
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


# A self-contained program run in fresh subprocesses under different PYTHONHASHSEED values:
# it builds the manifest from a FIXED run and prints only its content_hash. Kept in lockstep
# with _result() above; the duplication is deliberate -- a subprocess cannot import this test
# module, and a shared importable fixture would mean shipping test scaffolding inside the
# package. If you change _result(), change this too.
_DETERMINISM_PROGRAM = """
import bioforge.tools  # noqa: F401 -- register tools so reference_data_keys resolve
from bioforge.agent.loop import AgentResult, AgentStep
from bioforge.provenance import build_run_manifest

steps = [
    AgentStep(
        idx=0, type="tool_call", duration_ms=5, tool_name="find_offtargets",
        tool_input={"guide": "ACGTACGTACGTACGTACGT", "database": "nt"},
        tool_output={
            "tool_name": "find_offtargets", "tool_version": "1.0.0",
            "citations": ["Hsu PD 2013"], "hits": [],
        },
    ),
    AgentStep(
        idx=1, type="validation", duration_ms=2,
        verdict={
            "ok": True, "mode": "annotate", "enforced": False,
            "soundness": {"ok": True}, "ood": {"ok": True},
        },
    ),
]
result = AgentResult(
    goal="find off-targets", project_id="p",
    response_text="No off-targets found.", steps=steps, model="claude-sonnet-4-6",
)
print(build_run_manifest(result).content_hash)
"""


def test_content_hash_is_byte_stable_across_processes() -> None:
    # The same-process "stable across builds" test cannot catch PYTHONHASHSEED-dependent
    # set/dict iteration order: two builds under one seed agree even if an unsorted set
    # leaks into the canonical payload. Re-running the same fixed run in FRESH processes
    # under different hash seeds is the honest "re-run reproduces byte-identically"
    # guarantee (section 10 / rule 19). Seed 0 disables salting; 1 and 2 are two distinct
    # salts -- so this spans the no-salt and salted regimes.
    hashes: list[str] = []
    for seed in ("0", "1", "2"):
        proc = subprocess.run(
            [sys.executable, "-c", _DETERMINISM_PROGRAM],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PYTHONHASHSEED": seed,
                # Hermetic: the import chain (db/engine.py) builds an async engine from
                # BIOFORGE_DB_URL at import time. Pin a valid in-memory async URL so this
                # subprocess never inherits a sibling test's leaked sync URL (test_migrations
                # sets os.environ["BIOFORGE_DB_URL"] to a sqlite:// URL that create_async_engine
                # rejects). Manifest building does no DB I/O, so in-memory never connects.
                "BIOFORGE_DB_URL": "sqlite+aiosqlite:///:memory:",
            },
        )
        assert proc.returncode == 0, proc.stderr
        hashes.append(proc.stdout.strip())
    assert all(len(h) == 64 for h in hashes), hashes
    assert len(set(hashes)) == 1, f"content_hash not byte-stable across PYTHONHASHSEED: {hashes}"


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
