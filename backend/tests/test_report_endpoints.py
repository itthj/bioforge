"""End-to-end tests for the provenance export endpoints.

Inserts a realistic persisted Trace (steps stored as JSON dicts, exactly as the agent loop
persists them) into the per-test DB, then drives the real FastAPI endpoints. This exercises
the full path the feature adds: rehydration of AgentResult/AgentStep from stored JSON →
build_run_manifest → to_ro_crate / render_methods_report → HTTP response framing.
"""

from __future__ import annotations

import pytest
from bioforge.constants import DEFAULT_PROJECT_ID
from bioforge.db.models import Trace

_TRACE_ID = "trace-report-e2e"


def _persisted_steps() -> list[dict]:
    """Step dicts shaped like asdict(AgentStep) — a plan, two tool calls, a validation, a final."""
    return [
        {"idx": 0, "type": "plan", "duration_ms": 10, "plan": {"summary": "GC then BLAST", "steps": []}},
        {
            "idx": 1,
            "type": "tool_call",
            "duration_ms": 5,
            "tool_name": "gc_content",
            "tool_input": {"sequence": "ATGCATGC"},
            "tool_output": {"gc_percent": 50.0, "tool_version": "1.0.0"},
        },
        {
            "idx": 2,
            "type": "tool_call",
            "duration_ms": 2000,
            "tool_name": "blast",
            "tool_input": {"sequence": "ATGCATGCATGCATGCATGC", "database": "nt"},
            "tool_output": {"top_hit": "Homo sapiens", "tool_version": "2.0.0"},
        },
        {
            "idx": 3,
            "type": "validation",
            "duration_ms": 30,
            "verdict": {
                "ok": True,
                "mode": "enforce",
                "enforced": True,
                "soundness": {"ok": True},
                "ood": {"ok": True},
            },
        },
        {"idx": 4, "type": "final", "duration_ms": 1, "tool_output": None},
    ]


@pytest.fixture
async def _seed_trace(test_session_maker):
    async with test_session_maker() as session:
        session.add(
            Trace(
                id=_TRACE_ID,
                project_id=DEFAULT_PROJECT_ID,
                goal="Compute GC content and BLAST the sequence against nt",
                response_text="The GC content is 50%. The top BLAST hit is Homo sapiens.",
                status="completed",
                model="claude-sonnet-4-test",
                steps=_persisted_steps(),
            )
        )
        await session.commit()
    return _TRACE_ID


async def test_manifest_endpoint_returns_content_hash_and_tools(streaming_client, _seed_trace) -> None:
    resp = await streaming_client.get(f"/traces/{_TRACE_ID}/manifest")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["content_hash"]) == 64
    tool_names = {t["tool"] for t in body["tools"]}
    assert {"gc_content", "blast"} <= tool_names
    # blast declares the ncbi_blast reference dataset; it should surface as a reference build.
    assert any(rb["key"] == "ncbi_blast" for rb in body["reference_builds"])
    # The grounding verdict from the validation step is summarized.
    assert body["grounding"] is not None and body["grounding"]["ok"] is True


async def test_ro_crate_endpoint_is_valid_jsonld(streaming_client, _seed_trace) -> None:
    resp = await streaming_client.get(f"/traces/{_TRACE_ID}/ro-crate")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/ld+json")
    crate = resp.json()
    assert "@context" in crate and "@graph" in crate
    # The metadata descriptor and a CreateAction for the run must be present.
    ids = {node.get("@id") for node in crate["@graph"]}
    assert "ro-crate-metadata.json" in ids and "#run" in ids


async def test_report_endpoint_is_markdown_methods_record(streaming_client, _seed_trace) -> None:
    resp = await streaming_client.get(f"/traces/{_TRACE_ID}/report")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert "attachment; filename=" in resp.headers["content-disposition"]
    md = resp.text
    assert "# BioForge computational methods record" in md
    assert "## Computational methods" in md
    assert "## References" in md
    # Tool versions and the BLAST citation must appear.
    assert "gc_content" in md and "blast" in md
    assert "Altschul" in md  # BLAST citation pulled from the registry
    # The final answer text was included because the report was rendered with the result.
    assert "top BLAST hit is Homo sapiens" in md


async def test_missing_trace_is_404(streaming_client) -> None:
    for suffix in ("manifest", "ro-crate", "report"):
        resp = await streaming_client.get(f"/traces/does-not-exist/{suffix}")
        assert resp.status_code == 404
