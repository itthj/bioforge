"""§10 reproducibility research-object — a deterministic, content-addressed run manifest.

BioForge's provenance-from-day-one pillar, made exportable. Given a finished `AgentResult`,
`build_run_manifest` produces a typed, RO-Crate-inspired lineage record: the goal, model,
status, a NON-SECRET settings fingerprint, and one entry per tool invocation carrying the
tool version, the sha256 of its canonical input and output, the reference datasets it
depended on (from the registry's `reference_data_keys`, with commit pins where BioForge
controls them), and citations. A `content_hash` over the reproducible fields (everything
except volatile wall-clock / usage data) gives one fingerprint: the same logical run hashes
identically; a changed input, tool version, or reference pin hashes differently.

Building + exporting the research object: a plain content-addressed JSON manifest AND an
RO-Crate 1.1 JSON-LD crate (`to_ro_crate` / `export_ro_crate`) — the community-standard
package a scientist attaches to a paper's methods section. Digest-pinned execution containers
are deeper infra tracked separately. Nothing here changes run behavior: it is a pure read over
a finished result and is never invoked automatically.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from bioforge.config import Settings
from bioforge.config import settings as _default_settings
from bioforge.tools.registry import REGISTRY

if TYPE_CHECKING:
    from bioforge.agent.loop import AgentResult

_SCHEMA_VERSION = "bioforge-research-object/1"

# RO-Crate 1.1 (community standard for packaging research data + provenance as JSON-LD).
_RO_CRATE_CONTEXT = "https://w3id.org/ro/crate/1.1/context"
_RO_CRATE_CONFORMS = "https://w3id.org/ro/crate/1.1"
_BIOFORGE_NS = "https://github.com/itthj/bioforge#"

# Provenance-relevant settings recorded in the fingerprint. Deliberately an ALLOWLIST so
# secrets (API keys, DB URLs) and PII (operator email) are never written into a research
# object. Add a key here only after confirming it is safe to publish.
_FINGERPRINT_KEYS: tuple[str, ...] = (
    "default_model",
    "max_agent_iterations",
    "grounding_enabled",
    "grounding_mode",
    "grounding_judge_enabled",
    "grounding_judge_model",
    "deepcrispr_enabled",
    "deepcrispr_runner",
    "deepcrispr_upstream_commit",
    "indelphi_upstream_commit",
    "lindel_enabled",
    "lindel_runner",
    "lindel_upstream_commit",
    "forecast_enabled",
    "forecast_runner",
    "forecast_docker_image",
    "azimuth_enabled",
    "azimuth_runner",
    "azimuth_upstream_commit",
)

# reference_data_keys that BioForge version-pins itself -> the settings field holding the pin.
# Keys absent here are live external services (NCBI, Ensembl, gnomAD, ...) we do not version-pin.
_REFERENCE_PINS: dict[str, str] = {
    "deepcrispr_weights": "deepcrispr_upstream_commit",
    "indelphi_weights": "indelphi_upstream_commit",
    "lindel_weights": "lindel_upstream_commit",
    "forecast_model": "forecast_docker_image",
    "azimuth_weights": "azimuth_upstream_commit",
}


class ToolInvocation(BaseModel):
    tool: str
    version: str = Field(description="Tool version stamped on the output (falls back to the registry spec version).")
    input_sha256: str = Field(description="sha256 of the canonical-JSON tool input.")
    output_sha256: str = Field(description="sha256 of the canonical-JSON tool output.")
    reference_data_keys: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)


class ReferenceBuild(BaseModel):
    key: str = Field(description="Reference-dataset key from the registry, e.g. 'ensembl_vep'.")
    pin: str | None = Field(
        default=None,
        description="Version/commit pin when BioForge controls it; None for a live external service.",
    )
    pinned: bool = Field(
        description="True when a pin is recorded; False = live external dependency, not version-pinned."
    )


class RunManifest(BaseModel):
    schema_version: str = Field(default=_SCHEMA_VERSION, description="Manifest schema identifier.")
    goal: str
    model: str
    status: str
    response_sha256: str = Field(description="sha256 of the final response text.")
    settings_fingerprint: dict[str, Any] = Field(description="Non-secret, provenance-relevant settings.")
    tools: list[ToolInvocation] = Field(default_factory=list)
    reference_builds: list[ReferenceBuild] = Field(default_factory=list)
    grounding: dict[str, Any] | None = Field(
        default=None, description="Compact grounding/validation summary if the run produced one."
    )
    content_hash: str = Field(description="sha256 over the reproducible fields (excludes created_at + usage).")
    created_at: str = Field(description="ISO-8601 build time. Volatile — deliberately excluded from content_hash.")


def _canonical(obj: Any) -> str:
    """Deterministic JSON for hashing: sorted keys, compact, stable under str-coercion."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_obj(obj: Any) -> str:
    return _sha256_text(_canonical(obj))


def _settings_fingerprint(s: Settings) -> dict[str, Any]:
    return {key: getattr(s, key) for key in _FINGERPRINT_KEYS if hasattr(s, key)}


def _tool_invocations(result: AgentResult) -> list[ToolInvocation]:
    invocations: list[ToolInvocation] = []
    for step in result.steps:
        if step.type != "tool_call" or step.tool_name is None:
            continue
        out = step.tool_output or {}
        spec = REGISTRY.get(step.tool_name)
        version = str(out.get("tool_version") or (spec.version if spec else ""))
        citations = list(out.get("citations") or (spec.citations if spec else []))
        reference_keys = list(spec.reference_data_keys) if spec else []
        invocations.append(
            ToolInvocation(
                tool=step.tool_name,
                version=version,
                input_sha256=_sha256_obj(step.tool_input or {}),
                output_sha256=_sha256_obj(out),
                reference_data_keys=reference_keys,
                citations=citations,
            )
        )
    return invocations


def _reference_builds(invocations: list[ToolInvocation], s: Settings) -> list[ReferenceBuild]:
    keys = sorted({key for inv in invocations for key in inv.reference_data_keys})
    builds: list[ReferenceBuild] = []
    for key in keys:
        pin_field = _REFERENCE_PINS.get(key)
        pin = getattr(s, pin_field, None) if pin_field else None
        builds.append(ReferenceBuild(key=key, pin=pin, pinned=pin is not None))
    return builds


def _grounding_summary(result: AgentResult) -> dict[str, Any] | None:
    for step in result.steps:
        if step.type == "validation" and step.verdict is not None:
            v = step.verdict
            summary: dict[str, Any] = {"ok": v.get("ok"), "mode": v.get("mode"), "enforced": v.get("enforced")}
            if isinstance(v.get("soundness"), dict):
                summary["soundness_ok"] = v["soundness"].get("ok")
            if isinstance(v.get("ood"), dict):
                summary["ood_ok"] = v["ood"].get("ok")
            return summary
    return None


def build_run_manifest(result: AgentResult, *, settings: Settings | None = None) -> RunManifest:
    """Build a deterministic, content-addressed research object from a finished run.

    Pure read over `result` — no side effects, no network, never invoked automatically (so
    run behavior is unchanged). The `content_hash` covers the reproducible fields only;
    `created_at` and token-usage are excluded, so the same logical run fingerprints
    identically across builds while a changed input / version / reference pin does not.
    """
    s = settings if settings is not None else _default_settings
    invocations = _tool_invocations(result)
    reference_builds = _reference_builds(invocations, s)
    grounding = _grounding_summary(result)
    response_sha256 = _sha256_text(result.response_text or "")
    fingerprint = _settings_fingerprint(s)

    # The reproducible payload — exactly the fields the content hash commits to. Built
    # explicitly (rather than dumping the manifest) so created_at and the hash itself are
    # excluded by construction.
    reproducible = {
        "schema_version": _SCHEMA_VERSION,
        "goal": result.goal,
        "model": result.model,
        "status": result.status,
        "response_sha256": response_sha256,
        "settings_fingerprint": fingerprint,
        "tools": [inv.model_dump() for inv in invocations],
        "reference_builds": [rb.model_dump() for rb in reference_builds],
        "grounding": grounding,
    }
    content_hash = _sha256_obj(reproducible)

    return RunManifest(
        goal=result.goal,
        model=result.model,
        status=result.status,
        response_sha256=response_sha256,
        settings_fingerprint=fingerprint,
        tools=invocations,
        reference_builds=reference_builds,
        grounding=grounding,
        content_hash=content_hash,
        created_at=datetime.now(UTC).isoformat(),
    )


def export_research_object(manifest: RunManifest, out_dir: str | Path) -> Path:
    """Write the manifest as indented JSON into `out_dir`, named by its content hash.

    Returns the path. The filename is content-addressed (`bioforge-run-<hash12>.json`) so
    re-exporting the same logical run overwrites rather than duplicates.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"bioforge-run-{manifest.content_hash[:12]}.json"
    path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return path


def to_ro_crate(manifest: RunManifest) -> dict[str, Any]:
    """Serialize a RunManifest as an RO-Crate 1.1 metadata document (JSON-LD, §10).

    RO-Crate is the community standard for packaging research data + provenance, so emitting it
    lets a scientist attach a BioForge run to a paper's methods in a tool-readable form. A
    faithful-but-minimal crate: the metadata descriptor + root Dataset, a CreateAction for the
    run, one SoftwareApplication per tool invocation (with version + input/output sha256 +
    citations), and one entity per reference build. BioForge-specific properties use the
    `bioforge:` namespace so the document stays valid JSON-LD. Deterministic.
    """
    graph: list[dict[str, Any]] = [
        {
            "@type": "CreativeWork",
            "@id": "ro-crate-metadata.json",
            "conformsTo": {"@id": _RO_CRATE_CONFORMS},
            "about": {"@id": "./"},
        },
        {
            "@id": "./",
            "@type": "Dataset",
            "name": f"BioForge run ({manifest.content_hash[:12]})",
            "description": manifest.goal,
            "identifier": manifest.content_hash,
            "dateCreated": manifest.created_at,
            "mainEntity": {"@id": "#run"},
            "hasPart": (
                [{"@id": f"#tool-{i}"} for i in range(len(manifest.tools))]
                + [{"@id": f"#ref-{rb.key}"} for rb in manifest.reference_builds]
            ),
        },
        {
            "@id": "#run",
            "@type": "CreateAction",
            "name": "BioForge agent run",
            "actionStatus": manifest.status,
            "object": manifest.goal,
            "instrument": {"@id": "#bioforge"},
            "result": {"@id": "#response"},
            "bioforge:model": manifest.model,
            "bioforge:schema_version": manifest.schema_version,
            "bioforge:content_hash": manifest.content_hash,
            "bioforge:settings_fingerprint": manifest.settings_fingerprint,
            "bioforge:grounding": manifest.grounding,
        },
        {"@id": "#bioforge", "@type": "SoftwareApplication", "name": "BioForge"},
        {
            "@id": "#response",
            "@type": "CreativeWork",
            "name": "Final response",
            "bioforge:sha256": manifest.response_sha256,
        },
    ]
    for i, inv in enumerate(manifest.tools):
        graph.append(
            {
                "@id": f"#tool-{i}",
                "@type": "SoftwareApplication",
                "name": inv.tool,
                "version": inv.version,
                "citation": inv.citations,
                "bioforge:input_sha256": inv.input_sha256,
                "bioforge:output_sha256": inv.output_sha256,
                "bioforge:reference_data_keys": inv.reference_data_keys,
            }
        )
    for rb in manifest.reference_builds:
        graph.append(
            {
                "@id": f"#ref-{rb.key}",
                "@type": "Dataset",
                "name": rb.key,
                "version": rb.pin,
                "bioforge:pinned": rb.pinned,
            }
        )
    return {"@context": [_RO_CRATE_CONTEXT, {"bioforge": _BIOFORGE_NS}], "@graph": graph}


def export_ro_crate(manifest: RunManifest, out_dir: str | Path) -> Path:
    """Write the RO-Crate metadata document into a content-addressed crate dir; return its path.

    Per the RO-Crate spec the metadata file is always named `ro-crate-metadata.json` at the
    crate root, so distinct runs go in distinct `bioforge-run-<hash12>/` dirs to avoid collision.
    """
    crate_dir = Path(out_dir) / f"bioforge-run-{manifest.content_hash[:12]}"
    crate_dir.mkdir(parents=True, exist_ok=True)
    path = crate_dir / "ro-crate-metadata.json"
    path.write_text(json.dumps(to_ro_crate(manifest), indent=2, ensure_ascii=False), encoding="utf-8")
    return path
