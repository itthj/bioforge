"""§10 methods report — render a content-addressed run into a publication-grade Markdown record.

`build_run_manifest` (sibling module) already produces the machine-readable lineage: tool
versions, input/output checksums, reference-dataset pins, a grounding summary, and one
`content_hash` fingerprint over the reproducible fields. That is the artifact a *machine*
consumes (and the RO-Crate 1.1 JSON-LD is its FAIR-packaged form). What it is NOT is the
artifact a *scientist* pastes into a paper.

`render_methods_report` closes that gap. Given the same `RunManifest` (and, optionally, the
`AgentResult` it was built from, for analysis parameters and the final answer text), it emits
a Markdown document organized the way a computational methods + reproducibility record is
expected to be:

  1. Header + stable run identifier (the content hash) and build date.
  2. Summary — scientific question (goal), result, model, status.
  3. Computational methods — past-tense prose naming each tool *with its version*, plus a
     parameters record. This is the paste-into-the-paper section.
  4. Software & tools — exact versions (a hard requirement of every reproducibility checklist).
  5. Reference data & databases — each dataset with its version pin, or an explicit
     "live external service — not version-pinned" flag (FAIR-Reusable provenance, stated honestly).
  6. Validation & grounding — BioForge's own verdict, reported as-is (never inflated).
  7. Reproducibility & provenance — the fingerprint, the non-secret settings, the checksum scheme.
  8. Data & code availability — the standard journal statement, pointing at the RO-Crate.
  9. References — deduplicated, numbered, in first-seen order (the tool citations are already
     full references).
 10. Limitations — point-estimates without intervals, live (drift-prone) reference data, and any
     status caveat. The honesty is the standard, not a footnote to it.

Design rules, mirroring the manifest module: a PURE function — no I/O, no network, never
fabricates. Where a field is absent it SAYS the field is absent rather than inventing one.
Deterministic for a given (manifest, result): the only volatile value surfaced is the
manifest's own `created_at`, which the manifest already excludes from its content hash.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bioforge.provenance.research_object import RunManifest

if TYPE_CHECKING:
    from bioforge.agent.loop import AgentResult

# Tool/database short names a methods reader recognizes, keyed by reference_data_key. Used only
# to make the reference-data section read naturally; an unknown key falls back to the key itself,
# so adding a new reference dataset never silently drops it from the report.
_REFERENCE_LABELS: dict[str, str] = {
    "ncbi_blast": "NCBI BLAST nucleotide database (nt)",
    "ensembl_vep": "Ensembl Variant Effect Predictor data",
    "clinvar": "NCBI ClinVar",
    "dbsnp": "NCBI dbSNP",
    "gnomad": "gnomAD",
    "rcsb_pdb": "RCSB Protein Data Bank",
    "alphafold_db": "AlphaFold Protein Structure Database",
    "interpro": "EBI InterPro",
    "sifts": "EBI SIFTS",
    "deepcrispr_weights": "DeepCRISPR model weights",
    "indelphi_weights": "inDelphi model weights",
    "lindel_weights": "Lindel model weights",
    "forecast_model": "FORECasT model image",
    "azimuth_weights": "Azimuth (Rule Set 2) model weights",
}

# Human-readable status framing. Anything not listed is rendered verbatim with no spin.
_STATUS_PHRASES: dict[str, str] = {
    "completed": "completed successfully",
    "completed_after_replan": "completed successfully after one automated replanning step",
    "critique_failed": "completed but did NOT pass the internal critic — see Limitations",
    "refused": "was refused by the planner (no analysis was run)",
    "error": "terminated with an error",
    "iteration_cap": "stopped at the iteration cap before a final answer",
    "pending_approval": "is paused awaiting user approval of an expensive/destructive step",
    "cancelled": "was cancelled before completion",
}

_MAX_PARAM_VALUE_CHARS = 60


def _h(level: int, text: str) -> str:
    return f"{'#' * level} {text}"


def _short_hash(content_hash: str, n: int = 12) -> str:
    return content_hash[:n] if content_hash else "(unknown)"


def _summarize_param_value(value: Any) -> str:
    """Compact, methods-readable rendering of one parameter value.

    Long strings (sequences!) are truncated with a length annotation — the exact bytes are
    already committed to via the per-tool ``input_sha256``, so the report stays readable
    without losing reproducibility.
    """
    if isinstance(value, str):
        if len(value) > _MAX_PARAM_VALUE_CHARS:
            return f"{value[:_MAX_PARAM_VALUE_CHARS]}… ({len(value)} chars)"
        return value
    if isinstance(value, (list, tuple)):
        if len(value) > 6:
            return f"[{len(value)} items]"
        return ", ".join(_summarize_param_value(v) for v in value)
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}={_summarize_param_value(v)}" for k, v in value.items()) + "}"
    return str(value)


def _tool_params_by_index(result: AgentResult | None) -> list[dict[str, Any]]:
    """Per-tool-call input parameters, in execution order — aligned 1:1 with manifest.tools.

    The manifest builds one ToolInvocation per ``tool_call`` step in order, so the i-th set of
    params here corresponds to the i-th manifest tool entry. Returns ``[]`` when no result is
    supplied (the report still renders from the manifest alone, just without parameter detail).
    """
    if result is None:
        return []
    return [s.tool_input or {} for s in result.steps if s.type == "tool_call" and s.tool_name is not None]


def _dedup_references(manifest: RunManifest) -> list[str]:
    """All tool citations, deduplicated, in first-seen order. These are already full references."""
    seen: dict[str, None] = {}
    for inv in manifest.tools:
        for citation in inv.citations:
            if citation not in seen:
                seen[citation] = None
    return list(seen.keys())


def _methods_prose(manifest: RunManifest, params: list[dict[str, Any]], ref_index: dict[str, int]) -> list[str]:
    """The past-tense Methods paragraph(s) — the part intended to be lifted into a manuscript."""
    lines: list[str] = []
    if not manifest.tools:
        lines.append(
            "No external analysis tools were invoked for this run; the response was produced "
            "directly by the language model and carries no tool-level provenance. See Limitations."
        )
        return lines

    sentences: list[str] = []
    for i, inv in enumerate(manifest.tools):
        version = inv.version or "unversioned"
        cite_marks = sorted({ref_index[c] for c in inv.citations if c in ref_index})
        cite_suffix = f" [{', '.join(str(n) for n in cite_marks)}]" if cite_marks else ""
        param_text = ""
        if i < len(params) and params[i]:
            rendered = "; ".join(f"{k} = {_summarize_param_value(v)}" for k, v in params[i].items())
            param_text = f" (parameters: {rendered})"
        ordinal = "First" if i == 0 else ("Finally" if i == len(manifest.tools) - 1 else "Next")
        sentences.append(f"{ordinal}, `{inv.tool}` (v{version}){cite_suffix} was run{param_text}.")

    lines.append(
        f"The analysis was orchestrated by BioForge using model `{manifest.model}` and comprised "
        f"{len(manifest.tools)} tool invocation(s), executed in the following order. " + " ".join(sentences)
    )
    return lines


def render_methods_report(manifest: RunManifest, result: AgentResult | None = None) -> str:
    """Render a `RunManifest` as a publication-grade Markdown methods/reproducibility record.

    Pure and deterministic for a given (manifest, result). Pass `result` to include the final
    answer text and per-tool parameters; omit it to render provenance-only from the manifest.
    """
    params = _tool_params_by_index(result)
    references = _dedup_references(manifest)
    ref_index = {citation: i + 1 for i, citation in enumerate(references)}
    short = _short_hash(manifest.content_hash)
    status_phrase = _STATUS_PHRASES.get(manifest.status, f"ended with status `{manifest.status}`")

    out: list[str] = []

    # 1. Header + stable identifier
    out.append(_h(1, "BioForge computational methods record"))
    out.append(
        f"*Run identifier (content hash):* `{short}` — *generated:* {manifest.created_at} — "
        f"*schema:* `{manifest.schema_version}`"
    )
    out.append(
        "> This document was generated automatically from a content-addressed run manifest. "
        "The run identifier above is a SHA-256 fingerprint over the reproducible fields of the "
        "run (goal, model, tool versions, input/output checksums, reference-data pins, grounding "
        "verdict); an identical logical run reproduces this identifier exactly."
    )

    # 2. Summary
    out.append(_h(2, "Summary"))
    out.append(f"**Objective.** {manifest.goal}")
    out.append(f"**Model.** `{manifest.model}`")
    out.append(f"**Run status.** This run {status_phrase}.")
    if result is not None and result.response_text:
        out.append("**Result.**")
        out.append("> " + result.response_text.replace("\n", "\n> "))
    else:
        out.append(
            "**Result.** Final answer text was not included in this export (manifest-only render); "
            f"its SHA-256 is `{_short_hash(manifest.response_sha256)}`."
        )

    # 3. Computational methods (the paste-into-the-paper section)
    out.append(_h(2, "Computational methods"))
    out.extend(_methods_prose(manifest, params, ref_index))

    # 4. Software & tools — exact versions
    out.append(_h(2, "Software and tools"))
    if manifest.tools:
        rows = [
            "| # | Tool | Version | Input SHA-256 | Output SHA-256 | References |",
            "|---|------|---------|---------------|----------------|------------|",
        ]
        for i, inv in enumerate(manifest.tools, start=1):
            cite_marks = sorted({ref_index[c] for c in inv.citations if c in ref_index})
            cites = ", ".join(str(n) for n in cite_marks) if cite_marks else "—"
            rows.append(
                f"| {i} | `{inv.tool}` | {inv.version or '—'} | "
                f"`{_short_hash(inv.input_sha256)}` | `{_short_hash(inv.output_sha256)}` | {cites} |"
            )
        out.append("\n".join(rows))
    else:
        out.append("No external tools were invoked for this run.")

    # 5. Reference data & databases — pins or explicit live-service honesty
    out.append(_h(2, "Reference data and databases"))
    if manifest.reference_builds:
        rows = [
            "| Dataset | Description | Version / pin | Provenance |",
            "|---------|-------------|---------------|------------|",
        ]
        for rb in manifest.reference_builds:
            label = _REFERENCE_LABELS.get(rb.key, rb.key)
            if rb.pinned:
                pin = f"`{rb.pin}`"
                prov = "Version-pinned (BioForge-controlled)"
            else:
                pin = "—"
                prov = "Live external service — **not version-pinned** (may drift; see Limitations)"
            rows.append(f"| `{rb.key}` | {label} | {pin} | {prov} |")
        out.append("\n".join(rows))
    else:
        out.append("No external reference datasets were consulted for this run.")

    # 6. Validation & grounding — BioForge's verdict, reported as-is
    out.append(_h(2, "Validation and grounding"))
    g = manifest.grounding
    if g is None:
        out.append(
            "No grounding/validation verdict was recorded for this run. The numeric and entity "
            "claims in the result were therefore **not** independently validated by BioForge's "
            "grounding stack; treat the result accordingly."
        )
    else:
        ok = g.get("ok")
        verdict_word = "PASSED" if ok else ("FAILED" if ok is not None else "was inconclusive")
        out.append(
            f"BioForge's grounding validation **{verdict_word}** for this run "
            f"(mode: `{g.get('mode', 'unknown')}`, enforced: `{g.get('enforced', 'unknown')}`)."
        )
        sub: list[str] = []
        if "soundness_ok" in g:
            sub.append(f"soundness check: {'ok' if g['soundness_ok'] else 'flagged'}")
        if "ood_ok" in g:
            sub.append(f"out-of-distribution check: {'ok' if g['ood_ok'] else 'flagged'}")
        if sub:
            out.append("Sub-checks — " + "; ".join(sub) + ".")
        if g.get("enforced") is False:
            out.append(
                "*Note:* validation ran in shadow mode (not enforced), so flagged claims were "
                "recorded but not redacted from the result."
            )

    # 7. Reproducibility & provenance
    out.append(_h(2, "Reproducibility and provenance"))
    out.append(
        f"The full run is fingerprinted by the SHA-256 content hash `{manifest.content_hash}`. "
        "Every tool invocation records the SHA-256 of its canonical-JSON input and output (tabulated "
        "above), so any change to an input, a tool version, or a reference-data pin yields a different "
        "fingerprint. Volatile fields (wall-clock time, token usage) are excluded from the hash by "
        "construction, so re-running the identical logical analysis reproduces the same identifier."
    )
    if manifest.settings_fingerprint:
        setting_lines = ["Provenance-relevant settings (non-secret allowlist):", ""]
        setting_lines += [
            f"- `{k}` = `{manifest.settings_fingerprint[k]}`" for k in sorted(manifest.settings_fingerprint)
        ]
        out.append("\n".join(setting_lines))

    # 8. Data & code availability
    out.append(_h(2, "Data and code availability"))
    out.append(
        "A machine-readable provenance package for this run is available as an RO-Crate 1.1 "
        "(JSON-LD) document and as the underlying JSON run manifest, both content-addressed by the "
        "run identifier above. Where reference data was drawn from live external services (flagged "
        "in *Reference data and databases*), those resources are governed by their respective "
        "providers and were accessed at run time rather than from a version-pinned local copy."
    )

    # 9. References
    out.append(_h(2, "References"))
    if references:
        out.append("\n".join(f"{i}. {citation}" for i, citation in enumerate(references, start=1)))
    else:
        out.append("No tool-level references were recorded for this run.")

    # 10. Limitations — stated plainly
    out.append(_h(2, "Limitations"))
    limitations: list[str] = []
    if manifest.status == "critique_failed":
        limitations.append(
            "The internal critic did not confirm that the result satisfies the objective; the "
            "answer is reported with its unresolved concerns and should be treated as provisional."
        )
    if any(not rb.pinned for rb in manifest.reference_builds):
        limitations.append(
            "One or more reference datasets are live external services that are not version-pinned; "
            "re-running later may yield different results if the upstream resource changes."
        )
    if manifest.grounding is None:
        limitations.append(
            "No grounding verdict was recorded, so claims in the result were not independently validated."
        )
    elif manifest.grounding.get("enforced") is False:
        limitations.append(
            "Grounding validation ran in shadow mode (not enforced); flagged claims were not removed "
            "from the result."
        )
    if not manifest.tools:
        limitations.append(
            "No analysis tools were invoked, so the result has no tool-level computational provenance."
        )
    limitations.append(
        "Point estimates produced by predictive models are reported without per-prediction "
        "uncertainty intervals unless a tool explicitly emits them."
    )
    out.append("\n".join(f"- {item}" for item in limitations))

    return "\n\n".join(out) + "\n"
