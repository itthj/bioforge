#!/usr/bin/env python
"""Bulk-load predictions + measured wet-lab outcomes into the feedback loop (Limitation #4).

Reads a CSV and drives the /predictions API so you can close the loop from a spreadsheet instead
of one curl at a time. stdlib only -- no install.

CSV columns (header row required):
  subject_key      required  -- the join key (guide seq, variant, sample id)
  assay            required  -- e.g. "on-target efficiency" or "P(pathogenic)"
  predicted_value  required  -- the platform's predicted number
  kind             optional  -- "regression" (default) or "probability" (outcome must be 0/1)
  observed_value   optional  -- the measured result; rows that have it get an outcome recorded
  source           optional  -- which tool/model produced the prediction

Usage:
  python scripts/load_outcomes.py results.csv --project default-project
  BIOFORGE_TOKEN=... python scripts/load_outcomes.py results.csv --base-url http://localhost:8000

Records every row as a prediction, then records outcomes for rows that carry observed_value.
Then GET /predictions/agreement?project_id=...&assay=... shows the recomputed reliability/calibration.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.request


def _post(base_url: str, path: str, body: dict, token: str) -> dict:
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310 -- operator-supplied local URL
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise SystemExit(f"[load] {path} -> HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise SystemExit(f"[load] cannot reach {base_url} ({e}). Is the backend running?") from e


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", help="Path to the CSV.")
    ap.add_argument("--project", default=os.environ.get("BIOFORGE_DEFAULT_PROJECT_ID", "default-project"))
    ap.add_argument("--base-url", default=os.environ.get("BIOFORGE_BASE_URL", "http://localhost:8000"))
    args = ap.parse_args()
    token = os.environ.get("BIOFORGE_TOKEN", "")

    with open(args.csv, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise SystemExit("[load] CSV has no data rows.")

    preds = [
        {
            "subject_key": r["subject_key"].strip(),
            "assay": r["assay"].strip(),
            "predicted_value": float(r["predicted_value"]),
            "kind": (r.get("kind") or "regression").strip(),
            "source": (r.get("source") or None),
        }
        for r in rows
    ]
    _post(args.base_url, "/predictions", {"project_id": args.project, "predictions": preds}, token)
    print(f"[load] recorded {len(preds)} predictions in project {args.project!r}.")

    # Outcomes, grouped by assay so a probability assay's 0/1 validation applies per group.
    by_assay: dict[str, list[dict]] = {}
    for r in rows:
        val = (r.get("observed_value") or "").strip()
        if val:
            by_assay.setdefault(r["assay"].strip(), []).append(
                {"subject_key": r["subject_key"].strip(), "observed_value": float(val)}
            )
    total = 0
    for assay, outs in by_assay.items():
        res = _post(
            args.base_url,
            "/predictions/outcomes",
            {"project_id": args.project, "assay": assay, "outcomes": outs},
            token,
        )
        matched = res.get("matched", 0)
        total += matched
        print(f"[load] assay {assay!r}: matched {matched} outcome(s).")
    print(f"[load] done. {total} outcome(s) recorded. Open the Feedback tab or GET /predictions/agreement.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
