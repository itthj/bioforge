"""PCR primer design via primer3.

Wraps `primer3-py`'s `design_primers` — primer3 IS the gold-standard implementation
in the field, so unlike the on-target / off-target tools, this one doesn't need to
hedge about which algorithm it uses. The caveats here are about what primer3 itself
doesn't do:

  - It does NOT verify primer specificity against a genome. A primer that's perfect
    by Tm / GC / length can still bind elsewhere. Compose with the `blast` tool
    (or a future dedicated `find_offtargets`-style primer-specificity tool) when
    cross-reactivity matters.
  - It does NOT model the downstream PCR's actual behavior — only the primer-design
    rules and thermodynamics. Things like secondary structure of the template,
    extreme template GC, or repetitive sequences will trip up the real PCR even
    when primer3 happily designs a pair.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

_DNA_CHARS = set("ACGTNacgtn")


class DesignPrimersInput(ToolInput):
    template: str = Field(
        ...,
        min_length=40,
        max_length=20_000,
        description=(
            "DNA template sequence to amplify from. Must include enough flanking "
            "context around the target region for primers to land outside it."
        ),
    )
    target_start: int | None = Field(
        default=None,
        ge=0,
        description=(
            "0-based start of the region the amplicon MUST span. The primers will "
            "flank this region. Leave None to let primer3 pick any valid pair."
        ),
    )
    target_end: int | None = Field(
        default=None,
        ge=1,
        description="0-based exclusive end of the target region (paired with target_start).",
    )
    product_size_min: int = Field(default=80, ge=40, le=5000)
    product_size_max: int = Field(default=300, ge=40, le=5000)
    primer_tm_min: float = Field(default=58.0, ge=40.0, le=80.0)
    primer_tm_max: float = Field(default=62.0, ge=40.0, le=80.0)
    primer_tm_optimal: float = Field(default=60.0, ge=40.0, le=80.0)
    primer_gc_min: float = Field(default=20.0, ge=0.0, le=100.0)
    primer_gc_max: float = Field(default=80.0, ge=0.0, le=100.0)
    primer_length_min: int = Field(default=18, ge=12, le=40)
    primer_length_max: int = Field(default=25, ge=12, le=40)
    primer_length_optimal: int = Field(default=20, ge=12, le=40)
    max_primer_pairs: int = Field(
        default=5,
        ge=1,
        le=20,
        description="How many top primer pairs to return (primer3 ranks internally).",
    )

    @field_validator("template")
    @classmethod
    def _validate_dna(cls, v: str) -> str:
        cleaned = "".join(v.split()).upper()
        if not cleaned:
            raise ValueError("template is empty after stripping whitespace")
        bad = set(cleaned) - {c.upper() for c in _DNA_CHARS}
        if bad:
            raise ValueError(f"template contains non-DNA characters: {sorted(bad)!r}")
        return cleaned

    @model_validator(mode="after")
    def _validate_ranges(self) -> DesignPrimersInput:
        if self.product_size_min > self.product_size_max:
            raise ValueError("product_size_min must be ≤ product_size_max")
        if self.primer_tm_min > self.primer_tm_max:
            raise ValueError("primer_tm_min must be ≤ primer_tm_max")
        if not (self.primer_tm_min <= self.primer_tm_optimal <= self.primer_tm_max):
            raise ValueError("primer_tm_optimal must be within [primer_tm_min, primer_tm_max]")
        if self.primer_gc_min > self.primer_gc_max:
            raise ValueError("primer_gc_min must be ≤ primer_gc_max")
        if self.primer_length_min > self.primer_length_max:
            raise ValueError("primer_length_min must be ≤ primer_length_max")
        if not (self.primer_length_min <= self.primer_length_optimal <= self.primer_length_max):
            raise ValueError("primer_length_optimal must be within [primer_length_min, primer_length_max]")
        if (self.target_start is None) != (self.target_end is None):
            raise ValueError("target_start and target_end must both be set, or both None")
        if self.target_start is not None and self.target_end is not None:
            if self.target_start >= self.target_end:
                raise ValueError("target_start must be < target_end")
            if self.target_end > len(self.template):
                raise ValueError(f"target_end ({self.target_end}) exceeds template length ({len(self.template)})")
        return self


class PrimerPair(BaseModel):
    rank: int = Field(description="0-based rank (primer3's internal scoring).")
    forward_sequence: str
    forward_tm: float = Field(description="Predicted Tm in °C, primer3's calc.")
    forward_gc_percent: float
    forward_start: int = Field(description="0-based position on the template.")
    forward_length: int
    reverse_sequence: str
    reverse_tm: float
    reverse_gc_percent: float
    reverse_start: int = Field(
        description=(
            "0-based position of the 3'-end of the reverse primer on the FORWARD "
            "strand of the template (i.e. the rightmost amplified base + 1)."
        ),
    )
    reverse_length: int
    product_size: int = Field(description="Predicted amplicon length in nt.")
    pair_penalty: float = Field(
        description=(
            "primer3's pair score (lower = better). Combines Tm deviation, GC bias, secondary-structure penalties, etc."
        ),
    )


class DesignPrimersOutput(ToolOutput):
    template_length: int
    target_start: int | None
    target_end: int | None
    primer_pairs: list[PrimerPair]
    num_returned: int
    primer3_warnings: list[str] = Field(
        default_factory=list,
        description=(
            "Any explain / warning strings primer3 returned for the LEFT/RIGHT/PAIR "
            "design attempts. Surfaces e.g. 'considered N, no valid pair' or "
            "'failed: high any compl' so the agent can explain when no primers are found."
        ),
    )
    caveats: list[str]


def _extract_pairs(result: dict[str, Any], max_pairs: int) -> list[PrimerPair]:
    """primer3's design_primers returns a flat dict keyed by index. Pivot to per-pair
    records — easier for downstream consumers (agent, frontend) than the raw form."""
    num_returned = int(result.get("PRIMER_PAIR_NUM_RETURNED", 0))
    pairs: list[PrimerPair] = []
    for i in range(min(num_returned, max_pairs)):
        left_loc = result[f"PRIMER_LEFT_{i}"]  # (start, length)
        right_loc = result[f"PRIMER_RIGHT_{i}"]  # (3'-end-on-fwd-strand, length)
        pairs.append(
            PrimerPair(
                rank=i,
                forward_sequence=result[f"PRIMER_LEFT_{i}_SEQUENCE"],
                forward_tm=float(result[f"PRIMER_LEFT_{i}_TM"]),
                forward_gc_percent=float(result[f"PRIMER_LEFT_{i}_GC_PERCENT"]),
                forward_start=int(left_loc[0]),
                forward_length=int(left_loc[1]),
                reverse_sequence=result[f"PRIMER_RIGHT_{i}_SEQUENCE"],
                reverse_tm=float(result[f"PRIMER_RIGHT_{i}_TM"]),
                reverse_gc_percent=float(result[f"PRIMER_RIGHT_{i}_GC_PERCENT"]),
                reverse_start=int(right_loc[0]),
                reverse_length=int(right_loc[1]),
                product_size=int(result[f"PRIMER_PAIR_{i}_PRODUCT_SIZE"]),
                pair_penalty=float(result.get(f"PRIMER_PAIR_{i}_PENALTY", 0.0)),
            )
        )
    return pairs


def _explain(result: dict[str, Any]) -> list[str]:
    """Aggregate primer3's explain strings — useful when no pairs are returned."""
    out: list[str] = []
    for key in ("PRIMER_LEFT_EXPLAIN", "PRIMER_RIGHT_EXPLAIN", "PRIMER_PAIR_EXPLAIN"):
        val = result.get(key)
        if val:
            out.append(f"{key}: {val}")
    return out


@register_tool(
    name="design_primers",
    description=(
        "Design PCR primer pairs around a target region of a DNA template, using "
        "primer3 (the gold-standard implementation in the field). Pass the template "
        "plus optional target_start / target_end coordinates to constrain where the "
        "amplicon must span; primer3 then ranks valid pairs by Tm / GC / length / "
        "secondary-structure penalties and returns the top candidates. Use when the "
        "user wants PCR primers, qPCR primers, amplicon-design, or wants to verify "
        "a CRISPR edit by PCR. The tool does NOT check primer specificity against a "
        "genome — compose with `blast` against the relevant reference if cross-"
        "reactivity matters."
    ),
    input_model=DesignPrimersInput,
    output_model=DesignPrimersOutput,
    version="1.0.0",
    citations=[
        "Untergasser A et al. (2012) Primer3 — new capabilities and interfaces. Nucleic Acids Res 40:e115",
        "Koressaar T, Remm M (2007) Enhancements and modifications of primer design program Primer3. Bioinformatics 23:1289-1291",
        "primer3-py bindings (Lee 2014)",
    ],
    cost_hint="cheap",
    destructive=False,
    tags=["sequence", "primer", "pcr", "design"],
)
async def design_primers(inp: DesignPrimersInput) -> DesignPrimersOutput:
    # Import locally — primer3 is a C extension with non-trivial import cost (~30ms)
    # that the agent shouldn't pay at registry-load time.
    from primer3 import bindings as primer3_bindings

    seq_args: dict[str, Any] = {
        "SEQUENCE_ID": "bioforge_template",
        "SEQUENCE_TEMPLATE": inp.template,
    }
    if inp.target_start is not None and inp.target_end is not None:
        # primer3's SEQUENCE_TARGET is (start, length) — primer pairs must FLANK this.
        seq_args["SEQUENCE_TARGET"] = [
            inp.target_start,
            inp.target_end - inp.target_start,
        ]

    global_args: dict[str, Any] = {
        "PRIMER_PRODUCT_SIZE_RANGE": [[inp.product_size_min, inp.product_size_max]],
        "PRIMER_MIN_TM": inp.primer_tm_min,
        "PRIMER_MAX_TM": inp.primer_tm_max,
        "PRIMER_OPT_TM": inp.primer_tm_optimal,
        "PRIMER_MIN_GC": inp.primer_gc_min,
        "PRIMER_MAX_GC": inp.primer_gc_max,
        "PRIMER_MIN_SIZE": inp.primer_length_min,
        "PRIMER_MAX_SIZE": inp.primer_length_max,
        "PRIMER_OPT_SIZE": inp.primer_length_optimal,
        "PRIMER_NUM_RETURN": inp.max_primer_pairs,
        # Surface primer3's explain strings on every run — invaluable when no pairs
        # come back (otherwise the agent can't tell the user WHY).
        "PRIMER_EXPLAIN_FLAG": 1,
    }

    try:
        result = primer3_bindings.design_primers(seq_args=seq_args, global_args=global_args)
    except Exception as e:  # noqa: BLE001
        raise ToolError(
            f"primer3 design call failed: {type(e).__name__}: {e}. "
            "Check that the constraint ranges are internally consistent and that "
            "the template doesn't contain N stretches that prevent any pair."
        ) from e

    pairs = _extract_pairs(result, inp.max_primer_pairs)
    warnings = _explain(result)

    return DesignPrimersOutput(
        template_length=len(inp.template),
        target_start=inp.target_start,
        target_end=inp.target_end,
        primer_pairs=pairs,
        num_returned=len(pairs),
        primer3_warnings=warnings,
        caveats=[
            "primer3 does NOT verify primer specificity against a genome. A primer "
            "that scores well here can still bind off-target elsewhere — compose "
            "with `blast` against the relevant reference if cross-reactivity matters.",
            "Tm values are primer3's nearest-neighbor calculation. Real-world PCR "
            "Tm depends on buffer composition, Mg2+ concentration, and template GC "
            "context — treat these as estimates, not exact temperatures.",
            "If `num_returned` is 0, see `primer3_warnings` for the explain strings "
            "primer3 returned for the LEFT / RIGHT / PAIR stages. Typical fixes: "
            "widen Tm or GC ranges, allow a longer product size, or check for N "
            "stretches in the template.",
        ],
    )
