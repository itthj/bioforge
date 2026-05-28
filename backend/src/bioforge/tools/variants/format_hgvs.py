"""Convert VCF-style coordinates into HGVS genomic notation.

Closes the composition gap between `parse_vcf` (which emits chrom + 1-based
pos + ref + alt[]) and `annotate_variant` (which wants HGVS like
`17:g.43106487T>G`). Pure-Python, deterministic, no network.

# HGVS genomic conventions implemented here

VCF emits left-aligned, anchored variants:
  - SNV:        ref='T',   alt='G',     pos=43106487 → 17:g.43106487T>G
  - Insertion:  ref='T',   alt='TCGA',  pos=43106487 → 17:g.43106487_43106488insCGA
  - Deletion:   ref='TCGA', alt='T',    pos=43106487 → 17:g.43106488_43106490del
  - Single del: ref='TC',  alt='T',     pos=43106487 → 17:g.43106488del
  - Delins:     ref='TC',  alt='GA',    pos=43106487 → 17:g.43106487_43106488delinsGA

For VCF-style anchored indels (ref/alt share their first base), we strip the
anchor and emit ranges starting at pos+1 (HGVS convention puts the
range on the affected bases, NOT the anchor).

For variants without a shared anchor (true delins), we use the
`{pos}_{pos+len(ref)-1}delins{alt}` form unchanged.

# What this tool does NOT do

  - Coding (c.) or protein (p.) HGVS notation — those need a transcript
    context and CDS coordinates, which is annotate_variant's job after
    Ensembl maps the genomic coordinates onto a transcript.
  - Three-prime alignment normalization (HGVS prefers right-aligned).
    VCF left-aligns; users wanting strict HGVS shifting should
    pre-normalize with `bcftools norm -f ref` before parse_vcf.
  - Multi-base substitutions split into multiple SNVs.

The output identifies which convention was used per allele in
`kind`, so the agent can disclose ambiguity when needed.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

HgvsKind = Literal["substitution", "insertion", "deletion", "delins"]

_DNA_RE = re.compile(r"^[ACGTNacgtn]+$")
_CHROM_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


class FormatHgvsInput(ToolInput):
    chrom: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description=(
            "Chromosome / contig name as it appears in the VCF (e.g. '17', 'chr17', "
            "'NC_000017.11'). The `strip_chr_prefix` option controls whether a leading "
            "'chr' is dropped — Ensembl wants it stripped; UCSC keeps it."
        ),
    )
    pos: int = Field(
        ...,
        ge=1,
        description="1-based VCF position of the anchor base (the leftmost base in REF).",
    )
    ref: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="REF allele exactly as in the VCF (ACGTN). Case is normalized to upper.",
    )
    alt: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "One or more ALT alleles. Multi-allelic sites produce one HGVS string "
            "per allele in the output, preserving input order."
        ),
    )
    strip_chr_prefix: bool = Field(
        default=True,
        description=(
            "If True (default), drop a leading 'chr' so output suits Ensembl REST. "
            "Set False to keep UCSC-style 'chr17' chrom prefixes."
        ),
    )

    @field_validator("ref")
    @classmethod
    def _validate_ref(cls, v: str) -> str:
        cleaned = v.upper()
        if not _DNA_RE.match(cleaned):
            raise ValueError(f"ref must be ACGTN-only; got {v!r}")
        return cleaned

    @field_validator("alt")
    @classmethod
    def _validate_alt(cls, v: list[str]) -> list[str]:
        cleaned: list[str] = []
        for a in v:
            up = a.upper()
            # Symbolic alts like <DEL>, <DUP>, *(spanning-deletion) are not in scope.
            if up.startswith("<") or up == "*":
                raise ValueError(
                    f"alt allele {a!r} is symbolic — not supported by this tool. "
                    "Symbolic / structural variants need a separate code path."
                )
            if not _DNA_RE.match(up):
                raise ValueError(f"alt allele must be ACGTN-only; got {a!r}")
            cleaned.append(up)
        return cleaned

    @field_validator("chrom")
    @classmethod
    def _validate_chrom(cls, v: str) -> str:
        if not _CHROM_RE.match(v):
            raise ValueError(f"chrom contains unexpected characters: {v!r}")
        return v


class HgvsAllele(BaseModel):
    """One HGVS string + classification."""

    alt: str = Field(description="The ALT allele this HGVS string encodes (uppercase).")
    hgvs: str = Field(description="HGVS genomic notation, e.g. '17:g.43106487T>G'.")
    kind: HgvsKind = Field(description="Which HGVS form was used: substitution / insertion / deletion / delins.")
    notes: str = Field(default="", description="Plain-English description of the change.")


class FormatHgvsOutput(ToolOutput):
    chrom: str = Field(description="Chromosome name as it appears in the HGVS output (post-strip).")
    alleles: list[HgvsAllele] = Field(description="One per input ALT, in input order.")
    caveats: list[str] = Field(default_factory=list)


# --- HGVS construction ---------------------------------------------------------------


def _format_substitution(chrom: str, pos: int, ref: str, alt: str) -> tuple[str, str]:
    return f"{chrom}:g.{pos}{ref}>{alt}", f"single-base substitution {ref}>{alt} at position {pos}"


def _format_insertion(chrom: str, pos: int, ref: str, alt: str) -> tuple[str, str]:
    """VCF anchored insertion: ref='T', alt='TCGA' → ins 'CGA' after pos."""
    inserted = alt[len(ref) :]
    if not inserted:
        raise ToolError(
            f"Anchored insertion has no inserted bases — ref={ref!r}, alt={alt!r}. "
            "Are ref and alt identical? That's not a variant."
        )
    return (
        f"{chrom}:g.{pos}_{pos + 1}ins{inserted}",
        f"insertion of {len(inserted)} nt ({inserted!r}) between positions {pos} and {pos + 1}",
    )


def _format_deletion(chrom: str, pos: int, ref: str, alt: str) -> tuple[str, str]:
    """VCF anchored deletion: ref='TCGA', alt='T' → del positions pos+1..pos+3."""
    del_start = pos + len(alt)  # first deleted position
    del_end = pos + len(ref) - 1  # last deleted position (inclusive)
    if del_end < del_start:
        raise ToolError(
            f"Deletion has invalid range — ref={ref!r}, alt={alt!r}, pos={pos}. "
            "Check anchor convention (ref and alt should share their leading base)."
        )
    if del_start == del_end:
        return (
            f"{chrom}:g.{del_start}del",
            f"single-base deletion at position {del_start}",
        )
    return (
        f"{chrom}:g.{del_start}_{del_end}del",
        f"deletion of {del_end - del_start + 1} nt spanning positions {del_start}-{del_end}",
    )


def _format_delins(chrom: str, pos: int, ref: str, alt: str) -> tuple[str, str]:
    """No shared anchor: emit explicit delins form across the full ref span."""
    end = pos + len(ref) - 1
    if pos == end:
        return f"{chrom}:g.{pos}delins{alt}", f"single-base delins {ref}→{alt} at position {pos}"
    return (
        f"{chrom}:g.{pos}_{end}delins{alt}",
        f"{len(ref)}-nt block replaced by {len(alt)}-nt sequence ({ref}→{alt}) spanning {pos}-{end}",
    )


def _classify_and_format(chrom: str, pos: int, ref: str, alt: str) -> tuple[HgvsKind, str, str]:
    """Determine which HGVS form fits this (ref, alt) pair and produce it.

    Returns (kind, hgvs_string, prose_notes).
    """
    if ref == alt:
        raise ToolError(f"REF equals ALT ({ref!r}) — not a variant. Filter no-call rows before formatting.")

    # SNV
    if len(ref) == 1 and len(alt) == 1:
        h, n = _format_substitution(chrom, pos, ref, alt)
        return "substitution", h, n

    # Anchored insertion: shared first base + alt longer than ref
    if len(alt) > len(ref) and alt.startswith(ref):
        h, n = _format_insertion(chrom, pos, ref, alt)
        return "insertion", h, n

    # Anchored deletion: shared first base + ref longer than alt
    if len(ref) > len(alt) and ref.startswith(alt):
        h, n = _format_deletion(chrom, pos, ref, alt)
        return "deletion", h, n

    # Anything else is delins (block substitution).
    h, n = _format_delins(chrom, pos, ref, alt)
    return "delins", h, n


# --- Tool ----------------------------------------------------------------------------


@register_tool(
    name="format_hgvs",
    description=(
        "Convert a VCF-style variant (chrom, pos, ref, alt) into HGVS genomic "
        "notation suitable for annotate_variant. Handles SNVs, anchored "
        "insertions, anchored deletions, and block substitutions (delins). "
        "Multi-allelic sites produce one HGVS string per ALT allele. Strips "
        "leading 'chr' from chromosome names by default (Ensembl convention). "
        "Use whenever the agent has a parse_vcf record and needs to feed it "
        "to annotate_variant. Pure-Python, deterministic, no network."
    ),
    input_model=FormatHgvsInput,
    output_model=FormatHgvsOutput,
    version="1.0.0",
    citations=[
        "den Dunnen JT et al. (2016) HGVS Recommendations for the Description of Sequence Variants: 2016 Update. Hum Mutat 37:564-569 (HGVS nomenclature)",
        "Danecek P et al. (2011) The variant call format and VCFtools. Bioinformatics 27:2156-2158 (VCF anchored-indel convention)",
    ],
    cost_hint="cheap",
    destructive=False,
    tags=["variants", "hgvs", "format"],
)
async def format_hgvs(inp: FormatHgvsInput) -> FormatHgvsOutput:
    chrom = inp.chrom
    if inp.strip_chr_prefix and chrom.lower().startswith("chr"):
        chrom = chrom[3:]
        if not chrom:
            raise ToolError(f"chrom became empty after stripping 'chr' prefix from {inp.chrom!r}")

    alleles: list[HgvsAllele] = []
    for alt in inp.alt:
        kind, hgvs, notes = _classify_and_format(chrom, inp.pos, inp.ref, alt)
        alleles.append(HgvsAllele(alt=alt, hgvs=hgvs, kind=kind, notes=notes))

    caveats: list[str] = []
    if len(inp.alt) > 1:
        caveats.append(
            "Multi-allelic site — one HGVS string emitted per ALT allele. Pass each to "
            "annotate_variant separately; consequences differ per allele."
        )
    if any("N" in a.alt or "N" in inp.ref for a in alleles):
        caveats.append(
            "Sequence contains 'N' — Ensembl will likely reject the HGVS. Resolve the "
            "ambiguity (call against a high-quality reference) before annotating."
        )
    if chrom != inp.chrom:
        caveats.append(
            f"Chromosome stripped: {inp.chrom!r} → {chrom!r} (Ensembl convention). "
            "Set strip_chr_prefix=False if you need UCSC-style 'chr17'."
        )
    caveats.append(
        "HGVS genomic positions are 1-based inclusive (same as VCF). This tool does NOT "
        "right-shift indels to comply with HGVS's 3' rule — pre-normalize with `bcftools "
        "norm -f reference.fa` if strict HGVS compliance is required."
    )

    return FormatHgvsOutput(chrom=chrom, alleles=alleles, caveats=caveats)
