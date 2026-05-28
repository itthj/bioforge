"""Layer 7 (detector) — execution-time soundness checks (BioForge v4 §4, §0).

Deterministic range/sanity checks on structured tool outputs: a value that violates a
known physical/biological bound is a failure, not a finding — a GC% of 150, a CFD of 1.4,
a negative e-value. This is the layer the Founding Principle insists on: it catches
*impossible* and *misinterpreted-but-real* values that output-grounding alone cannot see
("polishing the lock on the front door while the back door is open").

Precision-first: we only check fields whose bounds we are certain of. An unknown field is
left alone — we never invent a bound (that would be its own unsourced-constant sin).
Extend `_BOUNDS` / `_NON_NEGATIVE` as tools are added.

Scope of this slice: the deterministic *detector* + its report. Acting on a violation
(failing the step and replanning at execution time, per the §4.1 loop) is a deeper
executor change tracked separately; here the violation is detected and recorded.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable

from pydantic import BaseModel, Field

# Closed-interval bounds [lo, hi] keyed by exact leaf field name. Only fields whose units
# and range are certain from the tool definitions appear here.
_BOUNDS: dict[str, tuple[float, float]] = {
    "gc_percent": (0.0, 100.0),
    "pct_identity": (0.0, 100.0),
    "plddt": (0.0, 100.0),
    "on_target_score": (0.0, 1.0),
    "heuristic_score": (0.0, 1.0),
    "gc_score": (0.0, 1.0),
    "polyt_score": (0.0, 1.0),
    "mononuc_score": (0.0, 1.0),
    "selfcomp_score": (0.0, 1.0),
    "gc_component": (0.0, 1.0),
    "polyt_component": (0.0, 1.0),
    "position_component": (0.0, 1.0),
    "dinucleotide_component": (0.0, 1.0),
    "cfd_score": (0.0, 1.0),
    "mit_score": (0.0, 1.0),
}

# Fields that must be non-negative (no meaningful upper bound).
_NON_NEGATIVE: frozenset[str] = frozenset({"e_value"})


class SoundnessViolation(BaseModel):
    path: str = Field(description="JSON path of the offending value.")
    field: str = Field(description="The leaf field name that carries a known bound.")
    value: float
    bound: str = Field(description="The violated bound, e.g. '[0, 100]' or '>= 0'.")


class SoundnessReport(BaseModel):
    ok: bool
    checked: int = Field(description="Number of bounded values that were checked.")
    violations: list[SoundnessViolation] = Field(default_factory=list)


def _leaf(path: str) -> str:
    """Last path segment, with any trailing list index stripped: 'guides[0].gc_percent' -> 'gc_percent'."""
    seg = path.rsplit(".", 1)[-1]
    bracket = seg.find("[")
    return seg[:bracket] if bracket != -1 else seg


def _walk(obj: object, path: str, on_value: Callable[[str, float], None]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        if math.isfinite(obj):
            on_value(path, float(obj))
        return
    if isinstance(obj, dict):
        for key, val in obj.items():
            _walk(val, f"{path}.{key}" if path else str(key), on_value)
        return
    if isinstance(obj, (list, tuple)):
        for i, val in enumerate(obj):
            _walk(val, f"{path}[{i}]", on_value)


def check_soundness(tool_outputs: Iterable[dict]) -> SoundnessReport:
    """Check every bounded numeric field in the run's tool outputs against its known range."""
    violations: list[SoundnessViolation] = []
    counter = {"checked": 0}

    def visit(path: str, value: float) -> None:
        name = _leaf(path)
        if name in _BOUNDS:
            counter["checked"] += 1
            lo, hi = _BOUNDS[name]
            if not (lo <= value <= hi):
                violations.append(SoundnessViolation(path=path, field=name, value=value, bound=f"[{lo:g}, {hi:g}]"))
        elif name in _NON_NEGATIVE:
            counter["checked"] += 1
            if value < 0:
                violations.append(SoundnessViolation(path=path, field=name, value=value, bound=">= 0"))

    for output in tool_outputs:
        _walk(output, "", visit)
    return SoundnessReport(ok=not violations, checked=counter["checked"], violations=violations)
