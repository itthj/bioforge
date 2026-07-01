"""Detect antimicrobial resistance (AMR) genes in a nucleotide sequence via local
BLASTX against the CARD (Comprehensive Antibiotic Resistance Database) protein
reference set.

There is no public CARD/RGI submission API — RGI (the Resistance Gene Identifier,
CARD's own analysis tool) is designed to be run locally against a downloaded copy
of the database. So unlike most BioForge lookup tools, this one has no `remote`
mode: it always shells out to a locally-installed `blastx` binary, the same
architecture as `blast.py`'s local backend (binary check via `shutil.which`,
`asyncio.create_subprocess_exec` with the query piped in as FASTA on stdin,
`ToolError` with an install/setup hint on a missing binary or non-zero exit —
no silent fallback).

**One-time operator setup** (not done by this tool):
  1. Download CARD data (`card.mcmaster.ca/download`) and extract the protein
     reference FASTA (`protein_fasta_protein_homolog_model.fasta`).
  2. Build a local BLAST protein database:
     `makeblastdb -in protein_fasta_protein_homolog_model.fasta -dbtype prot -out <prefix>`
  3. Set `BIOFORGE_CARD_BLAST_DB=<prefix>` (or pass `card_db` per call).

**License gate:** CARD is free for non-commercial research/academic/government/
non-profit use; commercial (for-profit) use requires a written license from
McMaster University (card.mcmaster.ca/about). This tool therefore mirrors the
inDelphi consent-gate pattern in `config.py`: it refuses to run at all unless
`BIOFORGE_CARD_CONSENT_COMMERCIAL_LICENSE=true` is set, confirming the operator
has reviewed CARD's terms for their use case. This is a one-time deployment
setting, not a per-request flag — there is nothing per-request to consent to.

**Custom BLAST output format:** unlike `blast.py`'s XML output (outfmt 5), this
tool requests tabular outfmt 6 with two extra columns beyond the NCBI default —
`qlen` and `slen` (query and subject length) — because reference (CARD protein)
coverage, not just percent identity, is required to assign a confidence band.
Percent identity alone can look "high" over a short, partial alignment; adding
coverage catches that.

**CARD FASTA header parsing:** CARD protein reference headers look like
`gb|AAA25406.1|ARO:3002999|CblA-1` (accession | ARO term | gene name), but the
field order has not been perfectly stable release-to-release. Rather than parse
positionally, the ARO accession is pulled out with an `ARO:\\d+` regex applied
anywhere in the subject ID — robust to reordering, at the cost of not handling
a hypothetical future header format that drops the `ARO:` prefix entirely (rare;
would raise no error, just report `aro_accession=None` for that hit).
"""

from __future__ import annotations

import asyncio
import re
import shutil

from pydantic import BaseModel, Field, field_validator

from bioforge.config import settings
from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

_DNA_CHARS = set("ACGTNacgtn")
_ARO_RE = re.compile(r"ARO:\d+")

_OUTFMT = "6 qseqid sseqid pident length mismatch gapopen qstart qend sstart send evalue bitscore qlen slen"
_N_TABULAR_FIELDS = 14


class AmrDetectionInput(ToolInput):
    sequence: str = Field(
        ...,
        min_length=12,
        max_length=200_000,
        description="Query nucleotide sequence (raw residues, no FASTA header). Translated in all 6 frames by blastx.",
    )
    card_db: str | None = Field(
        default=None,
        description=(
            "Path prefix to the local CARD BLAST protein database (built with `makeblastdb "
            "-dbtype prot`). Defaults to the BIOFORGE_CARD_BLAST_DB deployment setting when omitted."
        ),
    )
    min_identity_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Minimum percent identity for a hit to be reported. Defaults to the BIOFORGE_CARD_MIN_IDENTITY_PCT deployment setting.",
    )
    min_coverage_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Minimum reference (CARD protein) coverage %% for a hit to be reported. Defaults to the BIOFORGE_CARD_MIN_COVERAGE_PCT deployment setting.",
    )
    evalue_threshold: float = Field(default=1e-5, gt=0.0, le=10.0, description="E-value threshold passed to blastx.")
    max_hits: int = Field(default=25, ge=1, le=200, description="Maximum number of AMR gene hits to return, ranked by bit score.")

    @field_validator("sequence")
    @classmethod
    def _validate_sequence(cls, v: str) -> str:
        cleaned = "".join(v.split())
        if not cleaned:
            raise ValueError("sequence is empty after stripping whitespace")
        bad = set(cleaned) - _DNA_CHARS
        if bad:
            raise ValueError(f"sequence must be nucleotide (A/C/G/T/N); found unexpected residues: {sorted(bad)!r}")
        return cleaned.upper()


class AmrHit(BaseModel):
    aro_accession: str | None = Field(description="Antibiotic Resistance Ontology accession, e.g. 'ARO:3002999'. None if not parseable from the subject header.")
    gene_name: str = Field(description="AMR gene name parsed from the CARD header, or the raw subject ID if unparseable.")
    subject_id: str = Field(description="Raw blastx subject ID (full CARD FASTA header token).")
    identity_percent: float
    reference_coverage_percent: float = Field(description="Alignment length / CARD protein length * 100, capped at 100.")
    alignment_length_aa: int
    query_start: int
    query_end: int
    subject_start: int
    subject_end: int
    e_value: float
    bit_score: float
    confidence: str = Field(description="'high' (>=90% identity AND >=90% reference coverage) or 'moderate' (passes the reporting thresholds but not both high-confidence bars).")


class AmrDetectionOutput(ToolOutput):
    query_length: int
    database: str
    n_hits: int
    hits: list[AmrHit]
    caveats: list[str] = Field(default_factory=list)


def _extract_gene_name(sseqid: str) -> str:
    """Best-effort gene name from a CARD header, tolerant of pipe-field reordering.

    Standard CARD layout is `gb|<accession>|ARO:<n>|<gene_name>`. We locate the
    ARO:<n> field by regex (not position) and take the field immediately after it;
    if ARO is the last field, fall back to the field before it. If no ARO field is
    found at all, return the raw ID (aro_accession will be None on the hit).
    """
    parts = [p for p in sseqid.split("|") if p]
    aro_idx = next((i for i, p in enumerate(parts) if _ARO_RE.fullmatch(p)), None)
    if aro_idx is None:
        return sseqid
    if aro_idx + 1 < len(parts):
        return parts[aro_idx + 1]
    if aro_idx > 0:
        return parts[aro_idx - 1]
    return sseqid


async def _run_local_amr_blast(*, database: str, sequence: str, evalue: float, max_target_seqs: int) -> str:
    """Run blastx locally against a CARD protein BLAST database. Returns raw outfmt-6
    tabular text (see _OUTFMT for column order). Factored out for test patching —
    never hits the network or filesystem in the test suite."""
    binary = shutil.which("blastx")
    if binary is None:
        raise ToolError(
            "blastx (NCBI BLAST+) not found in PATH. Install BLAST+ "
            "(https://www.ncbi.nlm.nih.gov/books/NBK279690/), then build a local CARD "
            "protein database: download CARD data (https://card.mcmaster.ca/download), "
            "then `makeblastdb -in protein_fasta_protein_homolog_model.fasta -dbtype prot "
            "-out <prefix>`, and set BIOFORGE_CARD_BLAST_DB=<prefix>."
        )

    cmd = [
        binary,
        "-db",
        database,
        "-outfmt",
        _OUTFMT,
        "-evalue",
        str(evalue),
        "-max_target_seqs",
        str(max_target_seqs),
    ]
    fasta = f">query\n{sequence}\n"
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(fasta.encode("ascii"))
    except FileNotFoundError as e:
        raise ToolError("blastx binary disappeared between check and exec.") from e

    if proc.returncode != 0:
        err_text = stderr.decode("utf-8", errors="replace").strip()
        raise ToolError(
            f"Local blastx against the CARD database failed with exit {proc.returncode}. "
            f"stderr: {err_text or '(empty)'}. Verify the database {database!r} exists "
            "(no .phr/.pin/.psq files? run makeblastdb -dbtype prot)."
        )

    return stdout.decode("utf-8", errors="replace")


def _parse_amr_hits(tabular_text: str, min_identity: float, min_coverage: float, max_hits: int) -> list[AmrHit]:
    hits: list[AmrHit] = []
    for line in tabular_text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        fields = line.split("\t")
        if len(fields) < _N_TABULAR_FIELDS:
            continue  # malformed/truncated line — skip defensively rather than crash the whole run
        (
            _qseqid,
            sseqid,
            pident,
            length,
            _mismatch,
            _gapopen,
            qstart,
            qend,
            sstart,
            send,
            evalue,
            bitscore,
            _qlen,
            slen,
        ) = fields[:_N_TABULAR_FIELDS]

        try:
            pident_f = float(pident)
            length_i = int(length)
            slen_i = int(slen)
            evalue_f = float(evalue)
            bitscore_f = float(bitscore)
            qstart_i, qend_i, sstart_i, send_i = int(qstart), int(qend), int(sstart), int(send)
        except ValueError:
            continue  # a field wasn't numeric where expected — skip this line

        coverage = round(min(100.0, 100.0 * length_i / slen_i), 2) if slen_i > 0 else 0.0

        if pident_f < min_identity or coverage < min_coverage:
            continue

        aro_match = _ARO_RE.search(sseqid)
        aro = aro_match.group(0) if aro_match else None
        confidence = "high" if (pident_f >= 90.0 and coverage >= 90.0) else "moderate"

        hits.append(
            AmrHit(
                aro_accession=aro,
                gene_name=_extract_gene_name(sseqid),
                subject_id=sseqid,
                identity_percent=round(pident_f, 2),
                reference_coverage_percent=coverage,
                alignment_length_aa=length_i,
                query_start=qstart_i,
                query_end=qend_i,
                subject_start=sstart_i,
                subject_end=send_i,
                e_value=evalue_f,
                bit_score=bitscore_f,
                confidence=confidence,
            )
        )

    hits.sort(key=lambda h: h.bit_score, reverse=True)
    return hits[:max_hits]


@register_tool(
    name="amr_detection",
    description=(
        "Detect antimicrobial resistance (AMR) genes in a nucleotide sequence (assembled "
        "contig, plasmid, or amplicon) via local BLASTX against the CARD (Comprehensive "
        "Antibiotic Resistance Database) protein reference set. Use when the user asks "
        "'does this sequence carry any resistance genes', 'what AMR genes are in this "
        "genome/contig', or wants antibiotic-resistance annotation for bacterial sequence "
        "data. Returns ranked hits with the ARO (Antibiotic Resistance Ontology) accession, "
        "gene name, percent identity, reference coverage, and a high/moderate confidence "
        "band. REQUIRES local setup: a BLAST+ installation and a locally-built CARD protein "
        "BLAST database (see module docs) — there is no public CARD submission API. Also "
        "requires the BIOFORGE_CARD_CONSENT_COMMERCIAL_LICENSE deployment flag to be set, "
        "acknowledging CARD's non-commercial license terms."
    ),
    input_model=AmrDetectionInput,
    output_model=AmrDetectionOutput,
    version="1.0.0",
    citations=[
        "Alcock BP et al. (2023) CARD 2023: expanded curation, support for machine learning, "
        "and resistome prediction at the Comprehensive Antibiotic Resistance Database. "
        "Nucleic Acids Res 51(D1):D690-D699.",
        "Altschul SF et al. (1990) Basic local alignment search tool. J Mol Biol 215:403-410.",
        "CARD (https://card.mcmaster.ca)",
    ],
    cost_hint="moderate",
    tags=["functional", "amr", "antimicrobial-resistance", "microbiology", "blast"],
    reference_data_keys=["card"],
)
async def amr_detection(inp: AmrDetectionInput) -> AmrDetectionOutput:
    if not settings.card_consent_commercial_license:
        raise ToolError(
            "amr_detection requires BIOFORGE_CARD_CONSENT_COMMERCIAL_LICENSE=true to be set "
            "before it will run. CARD is free for non-commercial research/academic/government/"
            "non-profit use; commercial (for-profit) use requires a written license from "
            "McMaster University (https://card.mcmaster.ca/about). Setting this deployment "
            "flag confirms the operator has reviewed CARD's terms for their use case — it is "
            "a one-time setup step, not a per-request consent."
        )

    database = inp.card_db or settings.card_blast_db
    if not database:
        raise ToolError(
            "No CARD BLAST database configured. Set BIOFORGE_CARD_BLAST_DB to the path "
            "prefix of a local CARD protein BLAST database (built with `makeblastdb -in "
            "protein_fasta_protein_homolog_model.fasta -dbtype prot -out <prefix>` after "
            "downloading CARD data from https://card.mcmaster.ca/download), or pass "
            "card_db explicitly."
        )

    min_identity = inp.min_identity_pct if inp.min_identity_pct is not None else settings.card_min_identity_pct
    min_coverage = inp.min_coverage_pct if inp.min_coverage_pct is not None else settings.card_min_coverage_pct

    try:
        tabular_text = await _run_local_amr_blast(
            database=database,
            sequence=inp.sequence,
            evalue=inp.evalue_threshold,
            max_target_seqs=max(inp.max_hits, 50),  # over-fetch pre-filter; final cap applied after identity/coverage filtering
        )
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(f"Local blastx (CARD) call failed: {type(e).__name__}: {e}") from e

    hits = _parse_amr_hits(tabular_text, min_identity, min_coverage, inp.max_hits)

    caveats = [
        "AMR gene detection by sequence homology predicts presence of a resistance "
        "determinant, not confirmed phenotypic resistance — expression, regulation, and "
        "genetic context all affect whether a detected gene is functionally active.",
        f"Hits below {min_identity}% identity or {min_coverage}% reference coverage were "
        "filtered out before ranking; lowering these thresholds may surface additional, "
        "lower-confidence candidates.",
    ]
    if not hits:
        caveats.append(
            "No AMR genes were detected above the configured identity/coverage thresholds. "
            "This does not rule out resistance genes not represented in the current CARD "
            "database version, or genes present below the reporting thresholds."
        )

    return AmrDetectionOutput(
        query_length=len(inp.sequence),
        database=database,
        n_hits=len(hits),
        hits=hits,
        caveats=caveats,
    )
