"""The read_uploaded_file tool (Phase 6, slice 4): the agent reading the user's own data.

Exercised through the real registry (execute_tool) with the project/session ContextVars set, the
way an agent run invokes it. Storage is redirected to a tmp dir.
"""

from __future__ import annotations

import bioforge.tools.meta.file_tools as file_tools
import pytest
from bioforge.agent.context import AgentContextScope
from bioforge.db.models import UploadedFile
from bioforge.storage.adapter import LocalStorage
from bioforge.tools.base import ToolError
from bioforge.tools.registry import execute_tool

_FASTA = b">seq1 first record\nATGCATGCATGC\n>seq2\nTTTTGGGGCCCC\n"


@pytest.fixture
def tool_storage(tmp_path, monkeypatch):
    storage = LocalStorage(root_dir=str(tmp_path / "storage"))
    monkeypatch.setattr(file_tools, "get_storage", lambda: storage)
    return storage


async def _register_file(maker, storage, *, project_id: str, filename: str, data: bytes, key: str) -> None:
    meta = storage.put(project_id=project_id, key=key, data=data, content_type="text/plain")
    async with maker() as s:
        s.add(
            UploadedFile(
                project_id=project_id,
                filename=filename,
                storage_key=key,
                content_type="text/plain",
                size_bytes=meta.size_bytes,
                sha256=meta.sha256,
            )
        )
        await s.commit()


async def test_reads_and_parses_an_uploaded_fasta(test_session_maker, tool_storage) -> None:
    await _register_file(
        test_session_maker, tool_storage, project_id="proj", filename="seqs.fasta", data=_FASTA, key="uploads/f1"
    )
    async with test_session_maker() as s:
        with AgentContextScope(project_id="proj", session=s):
            out = await execute_tool("read_uploaded_file", {"filename": "seqs.fasta"})

    assert out.format == "fasta"
    assert out.size_bytes == len(_FASTA)
    assert out.fasta_records is not None
    assert [r.id for r in out.fasta_records] == ["seq1", "seq2"]
    assert out.fasta_records[0].description == "first record"
    assert out.fasta_records[0].sequence == "ATGCATGCATGC"  # the actual sequence, ready for design_guides
    assert not out.text_truncated


async def test_non_fasta_returns_text_only(test_session_maker, tool_storage) -> None:
    await _register_file(
        test_session_maker,
        tool_storage,
        project_id="proj",
        filename="variants.vcf",
        data=b"##fileformat=VCFv4.2\n#CHROM\tPOS\n1\t100\n",
        key="uploads/v1",
    )
    async with test_session_maker() as s:
        with AgentContextScope(project_id="proj", session=s):
            out = await execute_tool("read_uploaded_file", {"filename": "variants.vcf"})
    assert out.format == "vcf"
    assert out.fasta_records is None
    assert "VCFv4.2" in out.text


async def test_missing_file_errors_with_available_list(test_session_maker, tool_storage) -> None:
    await _register_file(
        test_session_maker, tool_storage, project_id="proj", filename="real.fasta", data=_FASTA, key="uploads/r1"
    )
    async with test_session_maker() as s:
        with AgentContextScope(project_id="proj", session=s):
            with pytest.raises(ToolError) as exc:
                await execute_tool("read_uploaded_file", {"filename": "ghost.fasta"})
    assert "real.fasta" in str(exc.value)  # tells the agent what IS available


async def test_isolated_to_the_current_project(test_session_maker, tool_storage) -> None:
    await _register_file(
        test_session_maker, tool_storage, project_id="proj-a", filename="a.fasta", data=_FASTA, key="uploads/a1"
    )
    # A run in a DIFFERENT project can't see proj-a's file.
    async with test_session_maker() as s:
        with AgentContextScope(project_id="proj-b", session=s):
            with pytest.raises(ToolError):
                await execute_tool("read_uploaded_file", {"filename": "a.fasta"})


async def test_requires_agent_context(test_session_maker, tool_storage) -> None:
    with pytest.raises(ToolError):
        await execute_tool("read_uploaded_file", {"filename": "anything.fasta"})
