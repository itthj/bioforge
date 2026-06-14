#!/usr/bin/env python
"""Regenerate the published §13 benchmark artifacts from your installed images + staged data.

Each subcommand runs ONE benchmark for real (the heavy, offline path -- never a page load) and
writes its provenance-stamped JSON into backend/src/bioforge/benchmarks/published/, which the
Accuracy Report then serves. Run with the project venv:

  .venv/Scripts/python.exe scripts/regenerate_benchmarks.py giab --sample "..." --slug ... ...
  .venv/Scripts/python.exe scripts/regenerate_benchmarks.py on-target
  .venv/Scripts/python.exe scripts/regenerate_benchmarks.py off-target
  .venv/Scripts/python.exe scripts/regenerate_benchmarks.py edit-outcome

Prerequisites are per-benchmark and are enforced by the underlying code with honest errors:
  - giab        -> BIOFORGE_DEEPVARIANT_ENABLED + image + the staged GIAB inputs (scripts/fetch_giab.sh)
  - on-target   -> BIOFORGE_DEEPCRISPR_ENABLED + image + BIOFORGE_CRISPOR_EFFDATA_CONSENT=true
  - off-target  -> BIOFORGE_CRISPOR_EFFDATA_CONSENT=true (CFD is in-platform; no image needed)
  - edit-outcome-> BIOFORGE_FORECAST_ENABLED + image + BIOFORGE_FORECAST_PROFILES_CONSENT=true
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the backend package importable when run from the repo root.
_SRC = Path(__file__).resolve().parents[1] / "backend" / "src"
sys.path.insert(0, str(_SRC))


def _giab(args: argparse.Namespace) -> Path:
    from bioforge.benchmarks.published import generate_giab_artifact

    return generate_giab_artifact(
        sample=args.sample,
        truth_set=args.truth_set,
        interpretation=args.interpretation,
        name=args.name,
        slug=args.slug,
    )


def _on_target(_args: argparse.Namespace) -> Path:
    from bioforge.benchmarks.published import generate_on_target_artifact

    return generate_on_target_artifact()


def _off_target(_args: argparse.Namespace) -> Path:
    from bioforge.benchmarks.published import generate_off_target_artifact

    return generate_off_target_artifact()


def _edit_outcome(_args: argparse.Namespace) -> Path:
    # Lives in a separate module in some builds; import lazily + tolerate either location.
    try:
        from bioforge.benchmarks.published import generate_edit_outcome_artifact  # type: ignore
    except ImportError:
        from bioforge.benchmarks.edit_outcome_published_run import generate_edit_outcome_artifact  # type: ignore
    return generate_edit_outcome_artifact()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("giab", help="GIAB variant-calling concordance + QUAL calibration")
    g.add_argument("--sample", required=True)
    g.add_argument("--truth-set", required=True)
    g.add_argument("--name", required=True)
    g.add_argument("--slug", required=True)
    g.add_argument("--interpretation", required=True)
    g.set_defaults(fn=_giab)

    sub.add_parser("on-target", help="DeepCRISPR x Chari-2015 on-target").set_defaults(fn=_on_target)
    sub.add_parser("off-target", help="CFD vs validated-site readFraction").set_defaults(fn=_off_target)
    sub.add_parser("edit-outcome", help="FORECasT vs measured K562 profiles").set_defaults(fn=_edit_outcome)

    args = p.parse_args()
    try:
        out = args.fn(args)
    except Exception as e:  # noqa: BLE001 -- surface the honest setup error to the operator
        print(f"\n[regenerate] {type(e).__name__}: {e}", file=sys.stderr)
        print("[regenerate] See docs/READINESS.md for the prerequisites of this benchmark.", file=sys.stderr)
        return 1
    print(f"[regenerate] Wrote {out}")
    print("[regenerate] Restart the backend (or reload) -- the Accuracy Report now serves it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
