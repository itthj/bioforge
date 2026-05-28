"""Layer 3 — deterministic numeric grounding (BioForge v4 §4, the floor of the system).

NO LLM is in this path. Numeric claims in a draft response are validated by
extraction + normalization + tolerance matching against the structured tool outputs
of the *same run*. This layer is expected to approach 100% precision-of-blocks
(it must never block a number that a tool actually produced) because it is the
deterministic foundation every higher layer trusts.

Design bias: **precision of blocks over recall of fabrications.** A wrongly blocked
real number erodes a scientist's trust instantly; a missed fabrication is caught by
later layers and the L6 corpus. So the numeric *inventory* (the set of "allowed"
values) is built generously — it includes numbers embedded in every string field,
citations, and caveats — while the claim *extractor* is conservative: it refuses to
treat numbers welded into biological identifiers as quantitative claims.

What the extractor deliberately does NOT treat as a numeric claim:
  - identifier-embedded digits: ``Cas9``, ``p53``, ``BRCA1``, ``hg38``, ``GRCh38``
    (letter immediately before the digit);
  - hyphenated identifiers: ``SARS-CoV-2``, ``COVID-19`` (letter-hyphen-digit);
  - sequence ends: ``5'``, ``3'`` (digit immediately before a prime);
  - ordinals: ``1st``, ``9th``;
  - HGVS / version fragments where a digit follows a dot: ``c.5266dupC``, ``v1.2``.

Known limitations (honestly stated; refined by the L2 classifier in a later slice):
  - bare structural integers in prose ("Step 1", "3D") are extracted and, if absent
    from tool outputs, reported unsupported — distinguishing a *finding* count from a
    *structural* integer is the claim classifier's job (L2), not this deterministic layer;
  - approximate scientific-notation phrasing (e.g. "~e-40" for 2.3e-40) is matched only
    within a loose mantissa tolerance; heavy rounding of e-values is deferred to L4/context.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass

from bioforge.agent.grounding.report import NumericClaimVerdict, ValidationReport

# --- Matching tolerances -------------------------------------------------------------
_REL_TOL = 1e-9  # "exact-ish" float equality
_SCI_REL_TOL = 1e-2  # looser tolerance for scientific-notation mantissa rounding
_ROUND_MIN = 1e-6  # below this magnitude, skip precision-rounding and use isclose only


# --- Number recognition --------------------------------------------------------------
#
# Draft extractor: guarded so identifier-embedded digits are NOT treated as quantitative
# claims. The two lookbehinds reject a digit that is glued to letters (Cas9, hg38) or to a
# letter-hyphen identifier (SARS-CoV-2); the lookaheads reject primes (5') and ordinals.
_DRAFT_NUMBER = re.compile(
    r"""
    (?<![\w.])                              # not preceded by a word-char or dot
    (?<![A-Za-z]-)                          # not a letter-hyphen-digit identifier
    (
        \d{1,3}(?:,\d{3})+(?:\.\d+)?        # comma-grouped: 43,000,000(.5)
        | \d+\.\d+(?:[eE][-+]?\d+)?         # decimal, optional sci: 0.78 / 1.5e-3
        | \d+[eE][-+]?\d+                   # integer-mantissa sci: 2e-40
        | \d+                               # plain integer: 20
    )
    (%?)                                    # optional trailing percent
    (?!['])                                 # not followed by a prime (5', 3')
    (?!(?:st|nd|rd|th)\b)                   # not an ordinal (1st, 9th)
    """,
    re.VERBOSE,
)

# Inventory extractor: permissive on purpose. We WANT every number that appears anywhere
# in a structured output (including inside strings, citations, coordinates) to count as
# groundable, so real claims are never wrongly blocked.
_INV_NUMBER = re.compile(
    r"\d{1,3}(?:,\d{3})+(?:\.\d+)?"  # comma-grouped
    r"|\d+\.\d+(?:[eE][-+]?\d+)?"  # decimal, optional sci
    r"|\d+[eE][-+]?\d+"  # integer-mantissa sci
    r"|\d+"  # plain integer
)


@dataclass(frozen=True)
class ParsedNumber:
    """A numeric token recognized in the draft response."""

    text: str  # surface form, e.g. "78%" or "0.78"
    value: float  # parsed value, commas/percent removed
    decimals: int  # digits after the decimal point (0 for integers / sci)
    is_percent: bool
    is_sci: bool
    start: int
    end: int


@dataclass(frozen=True)
class InventoryEntry:
    """One numeric value extracted from a structured tool output, with its origin."""

    path: str  # JSON path, e.g. "guides[0].gc_percent"
    value: float


def _parse_raw_number(raw: str, *, is_percent: bool) -> tuple[float, int, bool]:
    """Return (value, decimals, is_sci) for a recognized numeric substring."""
    cleaned = raw.replace(",", "")
    is_sci = "e" in cleaned or "E" in cleaned
    value = float(cleaned)
    if is_sci or "." not in cleaned:
        decimals = 0
    else:
        decimals = len(cleaned.split(".", 1)[1])
    return value, decimals, is_sci


def extract_numeric_claims(text: str) -> list[ParsedNumber]:
    """Find every quantitative numeric token in a draft response.

    Conservative by design — see the module docstring for what is deliberately skipped.
    """
    claims: list[ParsedNumber] = []
    for m in _DRAFT_NUMBER.finditer(text):
        raw = m.group(1)
        is_percent = m.group(2) == "%"
        value, decimals, is_sci = _parse_raw_number(raw, is_percent=is_percent)
        claims.append(
            ParsedNumber(
                text=text[m.start(1) : m.end()],
                value=value,
                decimals=decimals,
                is_percent=is_percent,
                is_sci=is_sci,
                start=m.start(1),
                end=m.end(),
            )
        )
    return claims


def _walk(obj: object, path: str, out: list[InventoryEntry]) -> None:
    """Recursively collect numeric values (and numbers embedded in strings) from a dict."""
    # bool is a subclass of int — exclude it before the numeric check.
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        if math.isfinite(obj):
            out.append(InventoryEntry(path=path or "(root)", value=float(obj)))
        return
    if isinstance(obj, str):
        for m in _INV_NUMBER.finditer(obj):
            try:
                value = float(m.group(0).replace(",", ""))
            except ValueError:  # pragma: no cover — regex guarantees a parseable number
                continue
            if math.isfinite(value):
                out.append(InventoryEntry(path=f"{path}#str", value=value))
        return
    if isinstance(obj, dict):
        for key, val in obj.items():
            child = f"{path}.{key}" if path else str(key)
            _walk(val, child, out)
        return
    if isinstance(obj, (list, tuple)):
        for i, val in enumerate(obj):
            _walk(val, f"{path}[{i}]", out)
        return
    # None / other scalar types carry no groundable number.


def build_inventory(tool_outputs: Iterable[dict]) -> list[InventoryEntry]:
    """Flatten every numeric value found anywhere in the run's structured tool outputs."""
    out: list[InventoryEntry] = []
    for output in tool_outputs:
        _walk(output, "", out)
    return out


def _value_matches(claim: ParsedNumber, inv_value: float) -> bool:
    """True if a claim's value can be reconciled with a structured tool value.

    Handles the percent/fraction duality (a 0.78 score quoted as "78%") and rounding
    ("roughly 0.78" for a stored 0.7843) without an LLM. Each candidate carries its own
    decimal precision so a percent's /100 form is rounded at the right scale.
    """
    candidates: list[tuple[float, int]] = [(claim.value, claim.decimals)]
    if claim.is_percent:
        candidates.append((claim.value / 100.0, claim.decimals + 2))

    for a, dec in candidates:
        if math.isclose(a, inv_value, rel_tol=_REL_TOL, abs_tol=0.0):
            return True
        if claim.is_sci:
            if math.isclose(a, inv_value, rel_tol=_SCI_REL_TOL, abs_tol=0.0):
                return True
            continue
        if abs(a) >= _ROUND_MIN and round(inv_value, dec) == round(a, dec):
            return True
    return False


def ground_response(response_text: str, tool_outputs: Iterable[dict]) -> ValidationReport:
    """Validate every numeric claim in `response_text` against the run's tool outputs.

    `tool_outputs` is the list of structured tool-result dicts produced this run
    (each typically `ToolOutput.model_dump()`). Returns a `ValidationReport`; `ok` is
    True iff no numeric claim was left unsupported.
    """
    inventory = build_inventory(tool_outputs)
    claims = extract_numeric_claims(response_text)

    verdicts: list[NumericClaimVerdict] = []
    for claim in claims:
        match: InventoryEntry | None = next((e for e in inventory if _value_matches(claim, e.value)), None)
        verdicts.append(
            NumericClaimVerdict(
                text=claim.text,
                value=claim.value,
                is_percent=claim.is_percent,
                start=claim.start,
                end=claim.end,
                status="grounded" if match is not None else "unsupported",
                matched_path=match.path if match is not None else None,
                matched_value=match.value if match is not None else None,
            )
        )

    unsupported = [v for v in verdicts if v.status == "unsupported"]
    ok = not unsupported
    grounded_n = len(verdicts) - len(unsupported)
    summary = (
        f"{grounded_n}/{len(verdicts)} numeric claims grounded against "
        f"{len(inventory)} tool values; {len(unsupported)} unsupported."
    )
    return ValidationReport(
        layer="L3_numeric",
        ok=ok,
        inventory_size=len(inventory),
        numeric_claims=verdicts,
        summary=summary,
    )
