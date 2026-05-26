"""VCF parser — first Phase 3 (variants & annotation) tool.

Parses VCFv4.x text blobs into structured variants the agent can reason over. Built
as a pure-Python parser rather than wrapping pysam/pyvcf because:

  - VCF is a stable, line-based text format. The 4.x spec is well-defined and the
    headers carry their own type information (##INFO=<...>, ##FORMAT=<...>).
  - pysam pulls in samtools + htslib C dependencies for build, which we don't need
    just to read variant records.
  - The agent's downstream use is straightforward (annotate, filter, summarize) —
    not bcftools-level transformations. A 200-line parser covers it.

What this tool does:
  - Reads the ##fileformat and ##INFO / ##FORMAT / ##contig / ##FILTER headers
  - Splits the body into one record per data line (chrom, pos, id, ref, alt, qual,
    filter, info, optional FORMAT + sample columns)
  - Decomposes the INFO field into a dict keyed by the header IDs (with primitive
    type coercion against the declared Number/Type)
  - Reports counts (SNVs, insertions, deletions, MNVs) and a `caveats` list

What this tool does NOT do:
  - Validate against the full VCF 4.5 grammar (multi-allelic genotype quirks,
    structural-variant breakend syntax, gVCF blocks). The most common single-sample
    callsets parse cleanly; exotic syntax surfaces in `parse_warnings` rather than
    raising.
  - Annotate variants against ClinVar / Ensembl VEP / gnomAD. That's the next slice
    (annotate_variant) — it composes with this parser, not embedded.
  - Read .vcf.gz directly. Pass the decompressed text; the agent can handle gzip
    upstream if needed.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from bioforge.tools.base import ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

VariantClass = Literal["SNV", "insertion", "deletion", "MNV", "complex", "unknown"]


class VcfHeader(BaseModel):
    fileformat: str | None = Field(default=None, description="VCF version, e.g. 'VCFv4.2'.")
    info_fields: dict[str, dict[str, str]] = Field(
        default_factory=dict,
        description=("Map from INFO key (e.g. 'DP', 'AF') to its header attributes (Number, Type, Description)."),
    )
    format_fields: dict[str, dict[str, str]] = Field(default_factory=dict)
    contigs: list[str] = Field(
        default_factory=list,
        description="Contig IDs declared in ##contig headers (order preserved).",
    )
    filters: dict[str, str] = Field(
        default_factory=dict,
        description="Map from FILTER id to its description.",
    )
    samples: list[str] = Field(
        default_factory=list,
        description="Sample column names from the #CHROM header line.",
    )


class Variant(BaseModel):
    chrom: str
    pos: int = Field(description="1-based position on the contig (VCF convention).")
    id: str | None
    ref: str
    alt: list[str] = Field(description="One entry per ALT allele. Multi-allelic sites stay as multiple alleles.")
    qual: float | None = Field(
        default=None,
        description="Phred-scaled quality. None when the source had '.'.",
    )
    filter: list[str] = Field(
        default_factory=list,
        description=(
            "FILTER status tokens. ['PASS'] means the variant passed all filters; "
            "any other ids name failed filters (the header's ##FILTER entries)."
        ),
    )
    info: dict[str, Any] = Field(
        default_factory=dict,
        description="Decomposed INFO dict — primitive types where the header declared them.",
    )
    variant_class: VariantClass = Field(
        description="Classification of the FIRST alt allele. Multi-allelic is surfaced in caveats."
    )


class ParseVcfInput(ToolInput):
    vcf_text: str = Field(
        ...,
        min_length=1,
        max_length=2_000_000,
        description=(
            "Full VCF text content (headers + body). Decompress .vcf.gz before "
            "passing. 2 MB cap is plenty for the agent's normal review-and-discuss "
            "workflow — for whole-callset analyses use a dedicated pipeline."
        ),
    )
    max_records: int = Field(
        default=200,
        ge=1,
        le=5000,
        description=(
            "Cap on returned variant records. Headers and counts are always emitted "
            "for the FULL input; truncation only affects which records ride along."
        ),
    )

    @field_validator("vcf_text")
    @classmethod
    def _must_look_like_vcf(cls, v: str) -> str:
        stripped = v.lstrip()
        if not stripped.startswith("##"):
            raise ValueError(
                "vcf_text does not begin with a '##fileformat=' (or other ##) header. "
                "Pass the full VCF including the header block."
            )
        return v


class ParseVcfOutput(ToolOutput):
    header: VcfHeader
    variants: list[Variant]
    num_records_total: int = Field(description="Number of data lines in the input (regardless of max_records cap).")
    num_records_returned: int
    counts_by_class: dict[str, int] = Field(description="Total counts across the FULL input (not just returned).")
    num_passing_filter: int = Field(description="Records whose FILTER is exactly 'PASS' (out of total).")
    parse_warnings: list[str] = Field(
        default_factory=list,
        description=(
            "Lines that failed to parse cleanly. Each entry names the line number "
            "and what went wrong. The tool does NOT raise on individual bad lines."
        ),
    )
    caveats: list[str]


# --- Header parsing -----------------------------------------------------------------

_HEADER_KV_RE = re.compile(r"^##(?P<key>[A-Za-z_]+)=(?P<value>.+)$")
_STRUCTURED_HEADER_RE = re.compile(r"<(?P<body>.+)>$")


def _parse_structured_header(value: str) -> dict[str, str]:
    """Parse e.g. `<ID=DP,Number=1,Type=Integer,Description="Total depth">`."""
    match = _STRUCTURED_HEADER_RE.match(value.strip())
    if not match:
        return {}
    body = match.group("body")
    # Split on commas, but not commas inside double-quoted strings.
    parts: list[str] = []
    buf: list[str] = []
    in_quotes = False
    for ch in body:
        if ch == '"':
            in_quotes = not in_quotes
            buf.append(ch)
        elif ch == "," and not in_quotes:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))

    out: dict[str, str] = {}
    for p in parts:
        if "=" not in p:
            continue
        k, _, v = p.partition("=")
        v = v.strip()
        if v.startswith('"') and v.endswith('"'):
            v = v[1:-1]
        out[k.strip()] = v
    return out


def _parse_header_block(lines: list[str]) -> tuple[VcfHeader, int]:
    """Return (header, index of first non-## line)."""
    header = VcfHeader()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip("\r\n")
        if not line.startswith("##"):
            break
        m = _HEADER_KV_RE.match(line)
        if not m:
            i += 1
            continue
        key, value = m.group("key"), m.group("value")
        if key == "fileformat":
            header.fileformat = value.strip()
        elif key == "INFO":
            attrs = _parse_structured_header(value)
            if "ID" in attrs:
                header.info_fields[attrs["ID"]] = attrs
        elif key == "FORMAT":
            attrs = _parse_structured_header(value)
            if "ID" in attrs:
                header.format_fields[attrs["ID"]] = attrs
        elif key == "contig":
            attrs = _parse_structured_header(value)
            if "ID" in attrs:
                header.contigs.append(attrs["ID"])
        elif key == "FILTER":
            attrs = _parse_structured_header(value)
            if "ID" in attrs:
                header.filters[attrs["ID"]] = attrs.get("Description", "")
        i += 1
    # The single `#CHROM ...` line names the sample columns.
    if i < len(lines) and lines[i].startswith("#CHROM"):
        cols = lines[i].rstrip("\r\n").split("\t")
        # Cols 0..8 are the fixed VCF columns; samples start at 9.
        if len(cols) > 9:
            header.samples = cols[9:]
        i += 1
    return header, i


# --- INFO decomposition --------------------------------------------------------------


def _coerce(value: str, info_attrs: dict[str, str] | None) -> Any:
    """Coerce a raw INFO string against the header's Type. Falls back to string."""
    if value == ".":
        return None
    if info_attrs is None:
        return value
    type_name = info_attrs.get("Type", "String")
    number = info_attrs.get("Number", "1")
    if "," in value or number not in ("0", "1"):
        return [_coerce(item, {**info_attrs, "Number": "1"}) for item in value.split(",")]
    try:
        if type_name == "Integer":
            return int(value)
        if type_name == "Float":
            return float(value)
        if type_name == "Flag":
            return True
    except ValueError:
        return value
    return value


def _decompose_info(raw: str, info_fields: dict[str, dict[str, str]]) -> dict[str, Any]:
    if raw in ("", "."):
        return {}
    out: dict[str, Any] = {}
    for chunk in raw.split(";"):
        if not chunk:
            continue
        if "=" not in chunk:
            # Flag-style key (e.g. "DB", "H3"). Header may or may not declare it.
            out[chunk] = True
            continue
        key, _, value = chunk.partition("=")
        out[key] = _coerce(value, info_fields.get(key))
    return out


# --- Variant classification ----------------------------------------------------------


def _classify(ref: str, alt: str) -> VariantClass:
    if alt in ("", "."):
        return "unknown"
    if len(ref) == 1 and len(alt) == 1:
        return "SNV"
    if len(ref) < len(alt) and alt.startswith(ref):
        return "insertion"
    if len(ref) > len(alt) and ref.startswith(alt):
        return "deletion"
    if len(ref) == len(alt):
        return "MNV"
    return "complex"


# --- Record parsing -----------------------------------------------------------------


def _parse_record(line: str, line_no: int, info_fields: dict[str, dict[str, str]]) -> tuple[Variant | None, str | None]:
    """Parse one body line. Returns (variant, error_message). Either is None."""
    cols = line.rstrip("\r\n").split("\t")
    if len(cols) < 8:
        return None, f"line {line_no}: expected ≥8 tab-separated columns, got {len(cols)}"
    try:
        chrom = cols[0]
        pos = int(cols[1])
        id_ = cols[2] if cols[2] != "." else None
        ref = cols[3]
        alt_raw = cols[4]
        alts = [a for a in alt_raw.split(",") if a] if alt_raw != "." else []
        qual = float(cols[5]) if cols[5] not in (".", "") else None
        filter_raw = cols[6]
        filter_list = (
            [filter_raw] if filter_raw == "PASS" else (filter_raw.split(";") if filter_raw not in (".", "") else [])
        )
        info = _decompose_info(cols[7], info_fields)
    except ValueError as e:
        return None, f"line {line_no}: numeric coercion failed ({e})"

    variant_class = _classify(ref, alts[0]) if alts else "unknown"
    return (
        Variant(
            chrom=chrom,
            pos=pos,
            id=id_,
            ref=ref,
            alt=alts,
            qual=qual,
            filter=filter_list,
            info=info,
            variant_class=variant_class,
        ),
        None,
    )


# --- Tool ---------------------------------------------------------------------------


@register_tool(
    name="parse_vcf",
    description=(
        "Parse a VCF text blob (Variant Call Format, the canonical genomic-variant "
        "interchange format) into structured variants. Returns the header (fileformat, "
        "INFO / FORMAT field definitions, contigs, filters), an array of decoded "
        "variant records, and counts by variant class (SNV / insertion / deletion / "
        "MNV / complex). Use when the user pastes VCF text, references a variant file, "
        "or wants to summarize / filter / annotate variants. Does NOT annotate against "
        "ClinVar or Ensembl VEP — that's a separate tool. Multi-allelic sites are "
        "preserved (alt is a list); the variant_class is computed for the first allele."
    ),
    input_model=ParseVcfInput,
    output_model=ParseVcfOutput,
    version="1.0.0",
    citations=[
        "Danecek P et al. (2011) The Variant Call Format and VCFtools. Bioinformatics 27:2156-2158",
        "VCF specification (https://samtools.github.io/hts-specs/VCFv4.3.pdf)",
    ],
    cost_hint="cheap",
    destructive=False,
    tags=["sequence", "variant", "vcf", "parsing"],
)
async def parse_vcf(inp: ParseVcfInput) -> ParseVcfOutput:
    lines = inp.vcf_text.splitlines()
    header, body_start = _parse_header_block(lines)

    counts: dict[str, int] = {}
    passing = 0
    parse_warnings: list[str] = []
    variants: list[Variant] = []
    num_total = 0

    body = lines[body_start:]
    for i, line in enumerate(body, start=body_start + 1):
        if not line.strip() or line.startswith("#"):
            continue
        num_total += 1
        variant, err = _parse_record(line, i, header.info_fields)
        if err:
            parse_warnings.append(err)
            continue
        assert variant is not None
        counts[variant.variant_class] = counts.get(variant.variant_class, 0) + 1
        if variant.filter == ["PASS"]:
            passing += 1
        if len(variants) < inp.max_records:
            variants.append(variant)

    caveats: list[str] = [
        "VCF parsing only — this tool does NOT annotate variants. Compose with a "
        "future `annotate_variant` tool (or query ClinVar / Ensembl VEP directly) "
        "for clinical significance or predicted impact.",
        "Multi-allelic sites stay multi-allelic (alt is a list). `variant_class` is "
        "computed for the first alt allele only; if a site has mixed classes (e.g. "
        "SNV + insertion at the same position) the agent should split it explicitly.",
    ]
    if num_total > inp.max_records:
        caveats.append(
            f"Returned {inp.max_records} of {num_total} records (max_records cap). "
            "Counts in `counts_by_class` and `num_passing_filter` cover the FULL input."
        )
    if header.fileformat and not header.fileformat.startswith("VCFv4"):
        caveats.append(
            f"Declared fileformat {header.fileformat!r} is not VCFv4.x; parsing was "
            "best-effort against the v4 grammar. Spot-check the records."
        )

    return ParseVcfOutput(
        header=header,
        variants=variants,
        num_records_total=num_total,
        num_records_returned=len(variants),
        counts_by_class=counts,
        num_passing_filter=passing,
        parse_warnings=parse_warnings,
        caveats=caveats,
    )
