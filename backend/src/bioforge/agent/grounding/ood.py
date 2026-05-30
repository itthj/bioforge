"""§6 model-honesty layer — out-of-distribution input detection + uncertainty posture.

Two deterministic, metadata-driven companions to the L7 soundness detector. Where
`soundness` checks tool OUTPUTS against known physical bounds, this layer looks at the
INPUTS a trained/weighted model was actually given, and at what each model's registry
metadata honestly says about its uncertainty:

* `check_ood` — flag inputs that fall outside a model's stated training/validity
  envelope (v4 §6 OOD gate). Precision-first, exactly like soundness: only tools whose
  declared envelope can be *exceeded by a schema-valid input* are checked. A tool that
  hard-validates its input to its envelope (e.g. `score_guide_on_target`'s exactly-20-nt
  protospacer) has nothing to flag. Extend `_OOD_CHECKERS` as trained scorers are added.

* `collect_model_uncertainty` — for each model-derived score that actually ran this turn,
  surface the §6 honesty note from `uncertainty_note` (report emitted uncertainty, else
  sourced published accuracy, else an explicit point-estimate framing — never a
  fabricated interval).

Scope of this slice: the deterministic *detector* + its records. Acting on a flag
(refusing or replanning at execution time, per the §4.1 loop) is a deeper executor
change tracked separately; here the flag is detected and recorded.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from pydantic import BaseModel, Field

from bioforge.tools.base import uncertainty_note
from bioforge.tools.registry import REGISTRY


class OODFlag(BaseModel):
    tool: str = Field(description="The tool whose model envelope the input fell outside.")
    field: str = Field(description="The input field that is out of envelope.")
    detail: str = Field(description="What the input actually was, e.g. 'guide length 18 nt'.")
    envelope: str = Field(description="The model's stated valid envelope, e.g. '20 nt (SpCas9)'.")
    message: str = Field(description="Scientist-facing explanation of the extrapolation risk.")


class OODReport(BaseModel):
    ok: bool = Field(description="True iff no input fell outside a known model envelope.")
    checked: int = Field(description="Number of tool calls inspected by a known OOD checker.")
    flags: list[OODFlag] = Field(default_factory=list)


class ModelUncertaintyNote(BaseModel):
    tool: str
    score_key: str = Field(description="Which declared score the note describes (e.g. 'on_target').")
    note: str = Field(description="The §6 honesty note from uncertainty_note().")


def _clean_seq(value: object) -> str | None:
    if isinstance(value, str):
        return "".join(value.split()).upper()
    return None


def _ood_find_offtargets(inp: dict) -> list[OODFlag]:
    """The MIT off-target score uses Hsu-2013 per-position weights defined for 20-nt SpCas9
    protospacers, but `find_offtargets` accepts 15-25 nt — so a non-20-nt guide is a
    schema-valid input that sits outside the score's stated envelope."""
    guide = _clean_seq(inp.get("guide"))
    if guide is None or len(guide) == 20:
        return []
    return [
        OODFlag(
            tool="find_offtargets",
            field="guide",
            detail=f"guide length {len(guide)} nt",
            envelope="20 nt (SpCas9; Hsu-2013 MIT off-target weights)",
            message=(
                f"find_offtargets' MIT score uses Hsu-2013 weights defined for 20-nt SpCas9 "
                f"protospacers; a {len(guide)}-nt guide is outside that envelope, so the "
                "off-target score is an extrapolation — treat it with extra caution."
            ),
        )
    ]


# Per-tool input-envelope checkers. Precision-first: only tools whose stated model
# envelope can be exceeded by a schema-valid input belong here. (Tools whose input is
# hard-validated to the envelope, like score_guide_on_target's 20-nt protospacer, have
# nothing to flag and are intentionally absent.)
_OOD_CHECKERS: dict[str, Callable[[dict], list[OODFlag]]] = {
    "find_offtargets": _ood_find_offtargets,
}


def check_ood(tool_calls: Iterable[tuple[str, dict]]) -> OODReport:
    """Flag inputs outside a model's stated envelope, over the run's tool calls.

    `tool_calls` is a sequence of `(tool_name, tool_input)`. Unknown tools (no registered
    checker) are skipped — precision-first, never a guessed envelope.
    """
    flags: list[OODFlag] = []
    checked = 0
    for name, inp in tool_calls:
        checker = _OOD_CHECKERS.get(name)
        if checker is None or not isinstance(inp, dict):
            continue
        checked += 1
        flags.extend(checker(inp))
    return OODReport(ok=not flags, checked=checked, flags=flags)


def ood_refusal(tool_name: str, tool_input: dict, *, mode: str) -> OODReport | None:
    """The §0/§4.3 OOD pre-gate decision, as a pure function the loop acts on.

    When `mode == "block"` and the input falls outside an involved model's stated envelope,
    return the OODReport to refuse on (the loop turns it into a refusal BEFORE the tool runs).
    Otherwise return None -- no pre-gate. `mode` is `settings.ood_gate`; the default "off"
    disables the pre-gate (the validator's post-response detector still records OOD), so the
    loop stays behaviorally identical until the flag is flipped.
    """
    if mode != "block":
        return None
    report = check_ood([(tool_name, tool_input)])
    return report if report.flags else None


def collect_model_uncertainty(tool_names: Iterable[str]) -> list[ModelUncertaintyNote]:
    """Surface the §6 uncertainty posture of each model-derived score that ran this turn.

    Considers each distinct tool name that has declared scorer metadata; emits one note per
    declared score key via `uncertainty_note`. Reports only declared metadata — it never
    invents an interval or an accuracy figure. Unknown tool names are skipped.
    """
    notes: list[ModelUncertaintyNote] = []
    seen: set[str] = set()
    for name in tool_names:
        if name in seen:
            continue
        seen.add(name)
        spec = REGISTRY.get(name)
        if spec is None:
            continue
        keys = sorted(set(spec.model_versions) | set(spec.emits_instance_uncertainty) | set(spec.published_accuracy))
        for key in keys:
            notes.append(ModelUncertaintyNote(tool=name, score_key=key, note=uncertainty_note(spec, key)))
    return notes


def summarize_ood(report: OODReport) -> str:
    """A compact scientist-facing OOD advisory, appended in annotate/enforce modes.

    Returns "" when there are no flags, so appending it is a no-op on in-envelope runs
    (which keeps the loop behaviorally identical for the common case).
    """
    if not report.flags:
        return ""
    lines = "\n".join(f"- {f.tool}.{f.field}: {f.detail} (envelope: {f.envelope})" for f in report.flags)
    return (
        "\n\n---\n[BioForge OOD] input(s) outside a model's validated envelope — "
        "the affected scores are extrapolations:\n" + lines
    )
