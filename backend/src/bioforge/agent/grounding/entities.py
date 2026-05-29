"""Deterministic structured-identifier grounding (BioForge v4 §4 — the entity floor).

Many entity claims are machine identifiers — rsIDs, RefSeq/Ensembl accessions, ClinVar
IDs, PDB codes — that either appear verbatim in the run's tool outputs (or the user's
request) or are fabricated. These need no LLM: we recognize them by their distinctive
shapes and check membership exactly.

Why this is safe to do deterministically (precision-first):
  - These shapes are essentially never normal background prose, so we are not flagging
    textbook knowledge. Gene symbols and free-text entities (the genuinely ambiguous
    cases) are left to the LLM judge (L4), not handled here.
  - An identifier the *user themselves supplied* is treated as grounded — echoing an input
    is not a fabrication — so the goal text is included as a grounding source.

A fabricated rsID or accession is exactly the "clinical inference they had no business
trusting" failure the founding principle warns about, so catching it for free (no model
call) is high-value.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from bioforge.agent.grounding.report import EntityClaimVerdict

# (kind, pattern). If the pattern has a capture group, group 1 is the identifier (used to
# keep a required keyword like "PDB" out of the captured id); otherwise the whole match.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("rsid", re.compile(r"(?i)\brs\d{2,}\b")),
    ("refseq", re.compile(r"\b[A-Z]{2}_\d+(?:\.\d+)?\b")),
    ("ensembl", re.compile(r"\bENS[A-Z]*\d{6,}(?:\.\d+)?\b")),
    ("clinvar", re.compile(r"\b[RV]CV\d{6,}(?:\.\d+)?\b")),
    ("pdb", re.compile(r"(?i)\bPDB[:\s]+([0-9][A-Za-z0-9]{3})\b")),
]


@dataclass(frozen=True)
class ParsedEntity:
    text: str
    kind: str
    start: int
    end: int


def _norm(s: str) -> str:
    return s.upper()


def extract_entity_claims(text: str) -> list[ParsedEntity]:
    """Find every structured biological identifier in `text`, de-duplicated by span."""
    found: list[ParsedEntity] = []
    seen: set[tuple[int, int]] = set()
    for kind, pat in _PATTERNS:
        for m in pat.finditer(text):
            has_group = bool(m.groups())
            ident = m.group(1) if has_group else m.group(0)
            start, end = (m.start(1), m.end(1)) if has_group else (m.start(0), m.end(0))
            if (start, end) in seen:
                continue
            seen.add((start, end))
            found.append(ParsedEntity(text=ident, kind=kind, start=start, end=end))
    return sorted(found, key=lambda e: e.start)


def _walk_strings(obj: object, path: str, out: list[tuple[str, str]]) -> None:
    if isinstance(obj, str):
        out.append((path or "(root)", obj))
        return
    if isinstance(obj, dict):
        for key, val in obj.items():
            _walk_strings(val, f"{path}.{key}" if path else str(key), out)
        return
    if isinstance(obj, (list, tuple)):
        for i, val in enumerate(obj):
            _walk_strings(val, f"{path}[{i}]", out)
    # numbers/bools/None carry no identifier


def build_identifier_index(tool_outputs: Iterable[dict], extra_sources: Iterable[str] = ()) -> dict[str, str]:
    """Map each identifier (normalized) found in tool outputs / extra sources to its origin path."""
    index: dict[str, str] = {}
    pairs: list[tuple[str, str]] = []
    for output in tool_outputs:
        _walk_strings(output, "", pairs)
    for i, src in enumerate(extra_sources):
        pairs.append((f"input[{i}]", src))
    for path, text in pairs:
        for ent in extract_entity_claims(text):
            index.setdefault(_norm(ent.text), path)
    return index


def ground_entities(
    response_text: str,
    tool_outputs: Iterable[dict],
    extra_sources: Iterable[str] = (),
) -> list[EntityClaimVerdict]:
    """Ground every structured identifier in the response against tool outputs + extra sources."""
    index = build_identifier_index(tool_outputs, extra_sources)
    verdicts: list[EntityClaimVerdict] = []
    for ent in extract_entity_claims(response_text):
        path = index.get(_norm(ent.text))
        verdicts.append(
            EntityClaimVerdict(
                text=ent.text,
                kind=ent.kind,
                start=ent.start,
                end=ent.end,
                status="grounded" if path is not None else "unsupported",
                matched_path=path,
            )
        )
    return verdicts
