"""Tests for bioforge.cli.fetch_alphafold.

The CLI is the bridge between Nextflow process blocks and the in-process
`fetch_alphafold_structure` tool. Tests cover: argparse surface, happy-path
JSON writeback, ToolError → non-zero exit + stderr message, --out parent
directory autocreation, --include-pdb-text plumbing.

execute_tool is monkeypatched so no network call ever fires.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from bioforge.cli import fetch_alphafold as cli_module
from bioforge.cli.fetch_alphafold import main
from bioforge.tools.base import ToolError


class _FakeStructure:
    """Stand-in for a FetchAlphaFoldOutput pydantic model — we only need model_dump()."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def model_dump(self) -> dict[str, Any]:
        return self._payload


def test_happy_path_writes_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    async def fake_execute(name: str, raw_input: dict[str, Any]) -> _FakeStructure:
        captured["name"] = name
        captured["input"] = raw_input
        return _FakeStructure({"uniprot_id": "P38398", "entry_id": "AF-P38398-F1"})

    monkeypatch.setattr(cli_module, "execute_tool", fake_execute)

    out_path = tmp_path / "alphafold_P38398.json"
    exit_code = main(["--uniprot", "P38398", "--out", str(out_path)])

    assert exit_code == 0
    assert captured["name"] == "fetch_alphafold_structure"
    assert captured["input"] == {
        "uniprot_id": "P38398",
        "include_pdb_text": False,
        "max_pdb_kb": 500,
    }
    assert out_path.exists()
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["uniprot_id"] == "P38398"
    assert written["entry_id"] == "AF-P38398-F1"


def test_include_pdb_text_flag_propagates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    async def fake_execute(name: str, raw_input: dict[str, Any]) -> _FakeStructure:
        captured["input"] = raw_input
        return _FakeStructure({"uniprot_id": "P38398"})

    monkeypatch.setattr(cli_module, "execute_tool", fake_execute)

    out_path = tmp_path / "alphafold_P38398.json"
    exit_code = main(
        [
            "--uniprot",
            "P38398",
            "--out",
            str(out_path),
            "--include-pdb-text",
            "--max-pdb-kb",
            "1000",
        ]
    )

    assert exit_code == 0
    assert captured["input"] == {
        "uniprot_id": "P38398",
        "include_pdb_text": True,
        "max_pdb_kb": 1000,
    }


def test_creates_parent_directory_for_out(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Nextflow processes may pass a nested out path; the CLI should mkdir -p."""

    async def fake_execute(name: str, raw_input: dict[str, Any]) -> _FakeStructure:
        return _FakeStructure({"uniprot_id": "P38398"})

    monkeypatch.setattr(cli_module, "execute_tool", fake_execute)

    nested = tmp_path / "a" / "b" / "c" / "alphafold_P38398.json"
    assert not nested.parent.exists()
    exit_code = main(["--uniprot", "P38398", "--out", str(nested)])
    assert exit_code == 0
    assert nested.exists()


def test_tool_error_returns_nonzero_and_writes_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_execute(name: str, raw_input: dict[str, Any]) -> _FakeStructure:
        raise ToolError("UniProt accession P99999 has no AlphaFold prediction")

    monkeypatch.setattr(cli_module, "execute_tool", fake_execute)

    out_path = tmp_path / "alphafold_P99999.json"
    exit_code = main(["--uniprot", "P99999", "--out", str(out_path)])

    assert exit_code == 1
    # On failure the output file should NOT be created — Nextflow's `output: path "...json" optional true`
    # handles the absence cleanly and the trace marks the task FAILED.
    assert not out_path.exists()
    err = capsys.readouterr().err
    assert "P99999" in err
    assert "no AlphaFold prediction" in err


def test_missing_required_args_exits_nonzero(tmp_path: Path) -> None:
    """argparse exits with code 2 on missing required args; we just confirm it's non-zero
    rather than pin to argparse's exact exit code."""
    with pytest.raises(SystemExit) as exc_info:
        main(["--out", str(tmp_path / "x.json")])  # missing --uniprot
    assert exc_info.value.code != 0
