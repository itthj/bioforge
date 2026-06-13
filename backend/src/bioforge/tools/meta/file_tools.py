"""Tool: read an uploaded data file so the agent can work on the user's OWN data.

This is the bridge from "bring your own data" (the upload API, Phase 6 slice 3) to the agent. The
user uploads a FASTA / VCF / table into their project; this tool loads it -- by filename, scoped to
the current project via the same ContextVars the memory tools use -- and returns its content (with
parsed FASTA records when applicable), which the agent then feeds into design_guides / parse_vcf /
gc_content / etc. Large content is capped so a whole genome can't blow the model context.
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field
from sqlalchemy import select

from bioforge.agent.context import get_current_db_session, get_current_project_id
from bioforge.db.models import UploadedFile
from bioforge.storage.adapter import StorageError, get_storage
from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

_MAX_TEXT_CHARS = 20_000  # decoded text returned to the model
_MAX_FASTA_RECORDS = 200  # records catalogued from a multi-FASTA
_MAX_INLINE_SEQ = 50_000  # per-record sequence inlined in full; longer -> length only


class ReadUploadedFileInput(ToolInput):
    filename: str = Field(
        min_length=1,
        max_length=255,
        description=(
            "Name of a file the user uploaded to this project (e.g. 'guides.fasta'). "
            "If several share the name, the most recent is used."
        ),
    )


class FastaRecord(BaseModel):
    id: str
    description: str
    length: int
    sequence: str | None = Field(
        description="The full sequence when short enough to inline; null for very long records (use the length)."
    )


class ReadUploadedFileOutput(ToolOutput):
    filename: str
    format: str = Field(description="Detected from the extension: fasta | vcf | bed | csv | tsv | genbank | text.")
    size_bytes: int
    sha256: str
    text: str = Field(description="Decoded file text (UTF-8), truncated if very large.")
    text_truncated: bool
    fasta_records: list[FastaRecord] | None = Field(
        default=None, description="Parsed records when the file is FASTA; null otherwise."
    )


def _detect_format(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in {"fasta", "fa", "fna", "ffn", "faa"}:
        return "fasta"
    return {
        "vcf": "vcf",
        "bed": "bed",
        "csv": "csv",
        "tsv": "tsv",
        "gb": "genbank",
        "gbk": "genbank",
        "genbank": "genbank",
    }.get(ext, "text")


def _parse_fasta(text: str) -> list[FastaRecord]:
    records: list[FastaRecord] = []
    header: str | None = None
    seq_parts: list[str] = []

    def _emit() -> None:
        if header is None:
            return
        rid, _, desc = header.partition(" ")
        seq = "".join(seq_parts)
        records.append(
            FastaRecord(
                id=rid or header,
                description=desc.strip(),
                length=len(seq),
                sequence=seq if len(seq) <= _MAX_INLINE_SEQ else None,
            )
        )

    for line in text.splitlines():
        if line.startswith(">"):
            _emit()
            if len(records) >= _MAX_FASTA_RECORDS:
                return records
            header = line[1:].strip()
            seq_parts = []
        elif header is not None:
            seq_parts.append(line.strip())
    _emit()
    return records


@register_tool(
    name="read_uploaded_file",
    description=(
        "Read a data file the user uploaded to this project (FASTA, VCF, BED, CSV/TSV, GenBank, or "
        "text), so you can run analyses on THEIR data. Give the filename. Returns the file text "
        "(truncated if large) and, for FASTA, the parsed records (id + sequence). Use the returned "
        "sequence as input to design_guides, gc_content, etc.; pass VCF/table text to parse_vcf. "
        "Errors (with the list of available files) if the filename isn't in this project."
    ),
    input_model=ReadUploadedFileInput,
    output_model=ReadUploadedFileOutput,
    version="1.0.0",
    citations=["BioForge project file storage"],
    cost_hint="cheap",
    destructive=False,
    tags=["data"],
)
async def read_uploaded_file(inp: ReadUploadedFileInput) -> ReadUploadedFileOutput:
    project_id = get_current_project_id()
    session = get_current_db_session()
    if not project_id or session is None:
        raise ToolError("read_uploaded_file has no project context. This tool is only callable inside an agent run.")

    row = (
        (
            await session.execute(
                select(UploadedFile)
                .where(UploadedFile.project_id == project_id, UploadedFile.filename == inp.filename)
                .order_by(UploadedFile.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        available = (
            (await session.execute(select(UploadedFile.filename).where(UploadedFile.project_id == project_id)))
            .scalars()
            .all()
        )
        hint = f" Available files: {sorted(set(available))}." if available else " This project has no uploaded files."
        raise ToolError(f"No uploaded file named {inp.filename!r} in this project.{hint}")

    storage = get_storage()
    try:
        data = await asyncio.to_thread(storage.get, project_id=project_id, key=row.storage_key)
    except StorageError as e:
        raise ToolError(f"Could not read {inp.filename!r} from storage: {e}") from e

    decoded = data.decode("utf-8", errors="replace")
    text = decoded[:_MAX_TEXT_CHARS]
    fmt = _detect_format(row.filename)
    return ReadUploadedFileOutput(
        filename=row.filename,
        format=fmt,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        text=text,
        text_truncated=len(decoded) > _MAX_TEXT_CHARS,
        fasta_records=_parse_fasta(decoded) if fmt == "fasta" else None,
    )
