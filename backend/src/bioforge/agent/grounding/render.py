"""Scientist-facing rendering of a grounding report (BioForge v4 §4).

Turns a `ValidationReport` into a concise, legible annotation a scientist reads beneath an
answer: either an affirmation that every quantitative / identifier / mechanistic claim was
traced to a tool result this run, or a clearly-marked caution listing what could not be.

This is the *trust signal* — grounding the scientist can see, not a buried trace step.
Unlike enforce mode it removes nothing; it tells the reader what to trust and what to
double-check, and lets them decide.
"""

from __future__ import annotations

from bioforge.agent.grounding.report import ValidationReport

_RULE = "\n\n---\n"


def summarize_grounding(report: ValidationReport) -> str:
    """Render a one-block grounding annotation, or '' when there is nothing to say.

    Returns '' for a response that makes no quantitative, identifier, or judged claim —
    no point cluttering a qualitative answer with a grounding badge.
    """
    n_numeric = len(report.numeric_claims)
    n_entity = len(report.entity_claims)
    n_judged = len([c for c in report.judged_claims if c.kind != "background"])
    if n_numeric + n_entity + n_judged == 0:
        return ""

    if report.ok:
        parts: list[str] = []
        if n_numeric:
            parts.append(f"{n_numeric} numeric")
        if n_entity:
            parts.append(f"{n_entity} identifier")
        if n_judged:
            parts.append(f"{n_judged} entity/mechanistic")
        return f"{_RULE}_Grounding check: all claims traced to tool results this run ({', '.join(parts)})._"

    lines: list[str] = []
    lines += [f'  - "{c.text}" (numeric)' for c in report.unsupported]
    lines += [f'  - "{e.text}" ({e.kind})' for e in report.unsupported_entities]
    lines += [f'  - "{jc.text}" ({jc.kind})' for jc in report.unsupported_judged]
    return (
        f"{_RULE}_Grounding check: the following claim(s) could not be traced to a tool result "
        f"this run -- treat with caution:_\n" + "\n".join(lines)
    )
