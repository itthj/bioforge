"""Typed records produced by the grounding validator (BioForge v4 §4).

These are the structured outputs of the grounding layers. Layer 3 (deterministic
numeric grounding) populates `numeric_claims`; later layers (L2 classifier, L4
entity/mechanistic judge) will extend this report rather than replace it.

Everything here is a typed Pydantic record on purpose: the validator's own output
must be as inspectable and serializable as the tool outputs it checks. A validator
that returns prose would reintroduce exactly the reinterpretation surface the rest
of the system eliminates.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# The four claim types from v4 §4 Layer 2. Slice 1 only *produces* "numeric"; the
# union is declared now so the classifier (next slice) and report consumers share one
# vocabulary instead of drifting.
ClaimKind = Literal["numeric", "entity", "mechanistic", "background"]

# A claim is either traceable to a structured tool result this run, or it is not.
GroundingStatus = Literal["grounded", "unsupported"]

# The judge's verdict on a non-numeric claim. "background" = general domain knowledge not
# asserted as a finding (permitted, but flagged so it reads as background, not a result).
JudgedStatus = Literal["supported", "unsupported", "background"]


class JudgedClaim(BaseModel):
    """An entity or mechanistic claim judged by the L4 LLM judge (BioForge v4 §4 Layer 4).

    The judge may ONLY support a claim by naming a `cited_field` that actually appears in
    the run's structured tool outputs — it is forbidden from supporting a claim with its
    own knowledge. This layer is treated as lossy and measured, never trusted blindly.
    """

    text: str = Field(description="The claim as it appears in the draft response.")
    kind: Literal["entity", "mechanistic", "background"]
    status: JudgedStatus
    cited_field: str | None = Field(
        default=None,
        description="The tool-output field path that supports this claim. Required for 'supported'.",
    )


class NumericClaimVerdict(BaseModel):
    """One numeric token found in the draft response, with its grounding outcome.

    `value` is the parsed numeric value (commas stripped, `%` removed). `text` keeps
    the surface form exactly as it appeared so the audit trail quotes the response,
    not a normalized rewrite.
    """

    text: str = Field(description="Surface form as it appears in the draft, e.g. '78%' or '0.92'.")
    value: float = Field(description="Parsed numeric value (commas stripped, percent sign removed).")
    is_percent: bool = Field(default=False, description="True if the surface form carried a '%'.")
    start: int = Field(description="Character offset of the token start in the draft.")
    end: int = Field(description="Character offset of the token end in the draft.")
    status: GroundingStatus
    matched_path: str | None = Field(
        default=None,
        description="JSON path of the tool-output field that grounded this claim, if grounded.",
    )
    matched_value: float | None = Field(
        default=None,
        description="The structured tool value this claim matched, if grounded.",
    )


class EntityClaimVerdict(BaseModel):
    """A structured biological identifier found in the draft, with its grounding outcome.

    Deterministic (no LLM): the identifier either appears verbatim in the run's tool
    outputs / the user's request, or it does not. `kind` names the identifier class
    (rsid, refseq, ensembl, clinvar, pdb).
    """

    text: str
    kind: str
    start: int
    end: int
    status: GroundingStatus
    matched_path: str | None = Field(
        default=None,
        description="Where the identifier was found (a tool-output path or 'input[i]'), if grounded.",
    )


class ValidationReport(BaseModel):
    """Result of running the grounding validator over a draft response.

    `ok` is the gate: in slice 1 it is True iff every numeric claim traced to a
    structured tool result. The wiring layer decides what to *do* with `ok` (block,
    redact, annotate) — the engine only reports.
    """

    layer: str = Field(default="L3_numeric", description="Which grounding layer produced this report.")
    ok: bool = Field(description="True iff no claim of the covered kinds was left unsupported.")
    inventory_size: int = Field(description="Number of distinct numeric values extracted from tool outputs.")
    numeric_claims: list[NumericClaimVerdict] = Field(default_factory=list)
    entity_claims: list[EntityClaimVerdict] = Field(
        default_factory=list,
        description="Structured identifiers (rsID, accession, ...) checked deterministically against tool outputs.",
    )
    judged_claims: list[JudgedClaim] = Field(
        default_factory=list,
        description="Entity/mechanistic claims judged by the L4 LLM judge (empty if the judge did not run).",
    )
    summary: str = Field(default="", description="One-line human-readable summary of the outcome.")

    @property
    def unsupported(self) -> list[NumericClaimVerdict]:
        """The numeric claims that could not be traced to a tool result this run."""
        return [c for c in self.numeric_claims if c.status == "unsupported"]

    @property
    def unsupported_entities(self) -> list[EntityClaimVerdict]:
        """The structured identifiers that could not be traced to a tool result or the user's input."""
        return [c for c in self.entity_claims if c.status == "unsupported"]

    @property
    def unsupported_judged(self) -> list[JudgedClaim]:
        """The entity/mechanistic claims the judge found unsupported by any tool result."""
        return [c for c in self.judged_claims if c.status == "unsupported"]
