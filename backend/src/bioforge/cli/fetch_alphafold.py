"""CLI entry point: fetch one AlphaFold-predicted structure to a JSON file.

Designed to be invoked by Nextflow process blocks:

    python -m bioforge.cli.fetch_alphafold --uniprot P38398 --out alphafold_P38398.json

The CLI is intentionally minimal — it just unpacks argparse arguments,
runs the registered `fetch_alphafold_structure` tool, and writes its
typed output to disk as JSON. Errors map to non-zero exit codes with a
single-line message on stderr; structured error info goes to the JSON
file ONLY when explicitly requested via `--error-to-out` so a Nextflow
trace can still distinguish process failures from successful tool
errors that the agent should reason about.

Usage from outside a workflow (debugging) is fine; the agent itself
never invokes this — it calls `execute_tool` directly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Importing `bioforge.tools` triggers tool registration side-effects via the
# package __init__ — without this the registry would be empty when invoked
# as `python -m bioforge.cli.fetch_alphafold` because the tools package is
# otherwise never touched.
import bioforge.tools  # noqa: F401  — registers all tools on import
from bioforge.tools.base import ToolError
from bioforge.tools.registry import execute_tool


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bioforge.cli.fetch_alphafold",
        description="Fetch one AlphaFold-predicted structure and write the typed JSON output to a file.",
    )
    parser.add_argument(
        "--uniprot",
        required=True,
        help="UniProt accession (e.g. P38398 for BRCA1).",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output path for the JSON-serialized FetchAlphaFoldOutput.",
    )
    parser.add_argument(
        "--include-pdb-text",
        action="store_true",
        help="Include the PDB text body in the output. Off by default to keep file size small.",
    )
    parser.add_argument(
        "--max-pdb-kb",
        type=int,
        default=500,
        help="Per-protein PDB-text cap in KB when --include-pdb-text is set. Default 500.",
    )
    return parser


async def _run_async(args: argparse.Namespace) -> int:
    """Run the tool and write its output. Returns the intended exit code."""
    try:
        result = await execute_tool(
            "fetch_alphafold_structure",
            {
                "uniprot_id": args.uniprot,
                "include_pdb_text": args.include_pdb_text,
                "max_pdb_kb": args.max_pdb_kb,
            },
        )
    except ToolError as e:
        # Tool-level failure (e.g. UniProt has no AlphaFold prediction). Report
        # to stderr so Nextflow surfaces it in the task .err file, exit non-zero
        # so the trace marks the step FAILED.
        sys.stderr.write(f"ToolError fetching AlphaFold for {args.uniprot!r}: {e}\n")
        return 1

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result.model_dump(), indent=2), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    """argparse + asyncio driver. Returns the exit code rather than calling sys.exit
    so tests can drive `main` and assert on the return value.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(_run_async(args))


if __name__ == "__main__":  # pragma: no cover — exercised via `python -m`
    sys.exit(main())
