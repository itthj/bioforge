"""BLAST tool — first `expensive` tool in the registry.

This is the canonical "search a sequence against a reference database" capability and the
first tool that exercises the approval gate. The implementation calls NCBI's public BLAST
web service via Biopython (`Bio.Blast.NCBIWWW.qblast`), which is synchronous and can take
30 seconds to several minutes depending on database + load. We push it into a thread so
the event loop stays free for other agent requests.

The expensive runtime + cost classification (`cost_hint="expensive"`) means a plan that
includes BLAST triggers the approval gate before any network call is made — see
`agent/approval.py`. The tool itself does not enforce approval; that's the loop's job.

Local BLAST+ binary integration is a later slice; this version is remote-only.
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

_DNA_CHARS = set("ACGTNacgtn")
_PROTEIN_CHARS = set("ACDEFGHIKLMNPQRSTVWYBXZJUO*acdefghiklmnpqrstvwybxzjuo")


class BlastProgram(str, Enum):
    """Which BLAST flavor to run. Choice determines which databases are valid."""

    blastn = "blastn"
    blastp = "blastp"
    blastx = "blastx"
    tblastn = "tblastn"


class BlastTask(str, Enum):
    """Sub-strategy within `blastn`. Required for short queries.

    - `megablast` (NCBI default): highly similar sequences, large query
    - `dc-megablast`: discontiguous megablast, more sensitive
    - `blastn`: traditional blastn, balanced
    - `blastn-short`: optimized for queries <30 nt (CRISPR guides, primers).
      Uses smaller word size + adjusted match/mismatch scoring.
    """

    megablast = "megablast"
    dc_megablast = "dc-megablast"
    blastn = "blastn"
    blastn_short = "blastn-short"


class BlastInput(ToolInput):
    sequence: str = Field(
        ...,
        description=(
            "Query sequence as a raw string of residues (DNA or protein, depending on "
            "program). FASTA headers and whitespace are not accepted — pass the bare "
            "sequence."
        ),
        min_length=12,
        max_length=50_000,
    )
    program: BlastProgram = Field(
        default=BlastProgram.blastn,
        description=(
            "BLAST flavor: blastn (nucleotide→nucleotide), blastp (protein→protein), "
            "blastx (translated nucleotide→protein), tblastn (protein→translated "
            "nucleotide). Choose based on what the query and target are."
        ),
    )
    database: str = Field(
        default="nt",
        description=(
            "NCBI database name. Common choices: 'nt' (nucleotide), 'nr' (protein), "
            "'refseq_select_rna', 'refseq_protein'. Database must be valid for the chosen "
            "program."
        ),
        min_length=1,
        max_length=64,
    )
    expect_threshold: float = Field(
        default=10.0,
        gt=0,
        le=10_000,
        description="E-value threshold; hits with E above this are excluded.",
    )
    max_hits: int = Field(
        default=10,
        ge=1,
        le=50,
        description=(
            "Maximum number of top hits to return. Capped at 50 to keep responses small "
            "enough for the agent to reason over."
        ),
    )
    task: BlastTask | None = Field(
        default=None,
        description=(
            "Sub-strategy for blastn. Default `None` lets NCBI pick (typically "
            "megablast). For queries <30 nt (CRISPR guides, primers) you almost always "
            "want `blastn-short` — megablast will miss most short matches. Ignored for "
            "blastp / blastx / tblastn."
        ),
    )

    @field_validator("sequence")
    @classmethod
    def _strip_whitespace(cls, v: str) -> str:
        cleaned = "".join(v.split())
        if not cleaned:
            raise ValueError("sequence is empty after stripping whitespace")
        return cleaned


class BlastHit(BaseModel):
    accession: str
    definition: str = Field(description="The hit's FASTA defline / description.")
    e_value: float
    bit_score: float
    identity_percent: float = Field(description="Percent identity of the top HSP.")
    alignment_length: int
    query_start: int
    query_end: int
    subject_start: int
    subject_end: int
    organism: str | None = None


class BlastOutput(ToolOutput):
    program: str
    database: str
    query_length: int
    num_hits_returned: int
    request_id: str = Field(
        description=(
            "NCBI Request ID (RID) for the BLAST search — reproducibility handle for "
            "later retrieval via the NCBI web interface."
        )
    )
    hits: list[BlastHit]


# --- NCBI call, factored out for test patching ---------------------------------------


async def _run_ncbi_blast(
    *,
    program: str,
    database: str,
    sequence: str,
    expect: float,
    hitlist_size: int,
    task: str | None = None,
) -> tuple[Any, str]:
    """Call NCBIWWW.qblast in a worker thread and parse the XML.

    Returns `(blast_record, request_id)`. Patched in tests so the suite never hits the
    network.
    """

    def _sync_call() -> tuple[Any, str]:
        from Bio.Blast import NCBIWWW, NCBIXML

        kwargs: dict[str, Any] = dict(
            program=program,
            database=database,
            sequence=sequence,
            expect=expect,
            hitlist_size=hitlist_size,
        )
        if task is not None:
            # NCBIWWW.qblast accepts megablast=True/False but routes "task" via the
            # `service` parameter for the WebUI. The cleanest cross-version handling is
            # to pass it as `megablast=True` when task=='megablast', and otherwise rely
            # on the explicit URL parameters Biopython adds. For Phase 1 first cut:
            # only translate "megablast" to the explicit flag and pass others as the
            # `task` keyword (Biopython >= 1.81 supports it).
            if task == "megablast":
                kwargs["megablast"] = True
            else:
                kwargs["megablast"] = False
            kwargs["task"] = task
        result_handle = NCBIWWW.qblast(**kwargs)
        rid = getattr(result_handle, "rid", "") or ""
        record = NCBIXML.read(result_handle)
        return (record, rid)

    return await asyncio.to_thread(_sync_call)


# --- Parsing -------------------------------------------------------------------------


def _parse_blast_record(record: Any, max_hits: int) -> list[BlastHit]:
    """Pull the top HSP from each alignment, up to max_hits."""
    hits: list[BlastHit] = []
    for alignment in record.alignments[:max_hits]:
        if not alignment.hsps:
            continue
        hsp = alignment.hsps[0]  # top HSP per alignment
        # `hit_def` may carry organism in brackets; surface it as a separate field.
        organism = None
        hit_def = alignment.hit_def or ""
        if "[" in hit_def and hit_def.rstrip().endswith("]"):
            organism = hit_def[hit_def.rfind("[") + 1 : hit_def.rfind("]")] or None

        align_len = hsp.align_length or 1
        identity_pct = round(100.0 * (hsp.identities or 0) / align_len, 2)

        hits.append(
            BlastHit(
                accession=alignment.accession or "",
                definition=hit_def,
                e_value=float(hsp.expect),
                bit_score=float(hsp.bits),
                identity_percent=identity_pct,
                alignment_length=int(hsp.align_length or 0),
                query_start=int(hsp.query_start or 0),
                query_end=int(hsp.query_end or 0),
                subject_start=int(hsp.sbjct_start or 0),
                subject_end=int(hsp.sbjct_end or 0),
                organism=organism,
            )
        )
    return hits


# --- Tool ----------------------------------------------------------------------------


@register_tool(
    name="blast",
    description=(
        "Search a sequence against an NCBI database using BLAST (Basic Local Alignment "
        "Search Tool). Use when the user asks to find similar sequences, identify a "
        "sequence, look up homologs, find off-targets for a guide RNA, or any 'what does "
        "this sequence match' question. Returns ranked hits with E-values, bit scores, "
        "and percent identity. EXPENSIVE: this calls NCBI's public BLAST service and can "
        "take 30 seconds to several minutes per query. The user will be asked to approve "
        "the run before it executes."
    ),
    input_model=BlastInput,
    output_model=BlastOutput,
    version="1.0.0",
    citations=[
        "Altschul SF et al. (1990) Basic local alignment search tool. J Mol Biol 215:403-410",
        "NCBI BLAST web service (https://blast.ncbi.nlm.nih.gov)",
        "Biopython Bio.Blast.NCBIWWW.qblast",
    ],
    cost_hint="expensive",
    destructive=False,
    tags=["sequence", "alignment", "search"],
)
async def blast(inp: BlastInput) -> BlastOutput:
    # Sanity-check residue alphabet against the chosen program before paying for the
    # network call. This catches "blastn with a protein sequence" early.
    seq = inp.sequence
    if inp.program in (BlastProgram.blastn, BlastProgram.tblastn):
        # blastn query is DNA; tblastn query is protein. So only blastn here.
        if inp.program == BlastProgram.blastn and not set(seq).issubset(_DNA_CHARS):
            bad = sorted(set(seq) - _DNA_CHARS)
            raise ToolError(
                f"blastn requires a DNA query (A/C/G/T/N). Found unexpected residues: {bad!r}."
            )
    if inp.program in (BlastProgram.blastp, BlastProgram.tblastn):
        if not set(seq).issubset(_PROTEIN_CHARS):
            bad = sorted(set(seq) - _PROTEIN_CHARS)
            raise ToolError(
                f"{inp.program.value} requires a protein query. Found unexpected residues: {bad!r}."
            )

    try:
        record, rid = await _run_ncbi_blast(
            program=inp.program.value,
            database=inp.database,
            sequence=seq,
            expect=inp.expect_threshold,
            hitlist_size=inp.max_hits,
            task=inp.task.value if inp.task else None,
        )
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(
            f"NCBI BLAST call failed: {type(e).__name__}: {e}. "
            "This is usually a transient network issue or NCBI rate-limiting; retry in a moment."
        ) from e

    hits = _parse_blast_record(record, inp.max_hits)

    return BlastOutput(
        program=inp.program.value,
        database=inp.database,
        query_length=len(seq),
        num_hits_returned=len(hits),
        request_id=rid,
        hits=hits,
    )
