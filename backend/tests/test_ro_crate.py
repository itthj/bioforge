"""§10 RO-Crate 1.1 JSON-LD export over the run manifest.

A scientist can attach a BioForge run to a paper's methods as a standard RO-Crate. We build
the crate directly from a RunManifest (no full agent run needed) and assert it is a valid
RO-Crate shape carrying the run's provenance.
"""

from __future__ import annotations

import json

from bioforge.provenance.research_object import (
    ReferenceBuild,
    RunManifest,
    ToolInvocation,
    export_ro_crate,
    to_ro_crate,
)


def _manifest() -> RunManifest:
    return RunManifest(
        goal="Find off-targets for an EMX1 guide",
        model="claude-sonnet-4-6",
        status="completed",
        response_sha256="a" * 64,
        settings_fingerprint={"default_model": "claude-sonnet-4-6", "grounding_mode": "annotate"},
        tools=[
            ToolInvocation(
                tool="find_offtargets",
                version="1.0.0",
                input_sha256="i" * 64,
                output_sha256="o" * 64,
                reference_data_keys=["ncbi_blast"],
                citations=["Hsu PD et al. (2013)"],
            )
        ],
        reference_builds=[ReferenceBuild(key="ncbi_blast", pin=None, pinned=False)],
        grounding={"ok": True, "mode": "annotate"},
        content_hash="d" * 64,
        created_at="2026-05-29T00:00:00+00:00",
    )


def test_ro_crate_has_valid_descriptor_and_root() -> None:
    crate = to_ro_crate(_manifest())
    assert crate["@context"][0] == "https://w3id.org/ro/crate/1.1/context"
    graph = crate["@graph"]
    ids = {e["@id"] for e in graph}
    assert "ro-crate-metadata.json" in ids  # the required metadata descriptor
    assert "./" in ids  # the root Dataset
    descriptor = next(e for e in graph if e["@id"] == "ro-crate-metadata.json")
    assert descriptor["about"] == {"@id": "./"}


def test_ro_crate_carries_tool_provenance() -> None:
    crate = to_ro_crate(_manifest())
    tools = [e for e in crate["@graph"] if e["@id"].startswith("#tool-")]
    assert len(tools) == 1
    t = tools[0]
    assert t["@type"] == "SoftwareApplication"
    assert t["name"] == "find_offtargets"
    assert t["version"] == "1.0.0"
    assert t["bioforge:output_sha256"] == "o" * 64
    # the run action records the content hash for reproducibility
    run = next(e for e in crate["@graph"] if e["@id"] == "#run")
    assert run["bioforge:content_hash"] == "d" * 64


def test_export_ro_crate_writes_metadata_file(tmp_path) -> None:
    path = export_ro_crate(_manifest(), tmp_path)
    assert path.name == "ro-crate-metadata.json"
    assert "bioforge-run-" in path.parent.name
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "@context" in data and "@graph" in data
