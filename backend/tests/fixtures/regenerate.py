"""Regenerate committed test fixtures from public sequence databases.

Run this once after cloning, or whenever you want to refresh fixtures against current
NCBI data. Any change to the upstream sequence (extremely unlikely for a finished genome
like lambda phage NC_001416.1, but possible for in-flux records) produces a clean git
diff in the committed FASTA + metadata files.

Requirements:
  - BIOFORGE_ENTREZ_EMAIL set in .env or environment (NCBI Entrez policy)
  - Network access to eutils.ncbi.nlm.nih.gov

The fixtures are NOT committed pre-populated — running this script is part of
post-clone setup. Tests that need a fixture skip with a clear message if it's missing.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from Bio import Entrez, SeqIO
from Bio.SeqUtils import gc_fraction

FIXTURE_DIR = Path(__file__).parent


def _require_email() -> str:
    email = os.environ.get("BIOFORGE_ENTREZ_EMAIL", "").strip()
    if not email:
        # Also try a .env file at the repo root, for the common case where the script
        # is run directly without first sourcing the env.
        env_path = Path(__file__).parents[3] / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("BIOFORGE_ENTREZ_EMAIL="):
                    email = line.split("=", 1)[1].strip().strip("\"'")
                    break
    if not email or "@" not in email:
        print(
            "ERROR: BIOFORGE_ENTREZ_EMAIL is not set. NCBI Entrez requires an email "
            "address for API access. Add it to .env:\n"
            "  BIOFORGE_ENTREZ_EMAIL=you@example.com",
            file=sys.stderr,
        )
        sys.exit(2)
    return email


def regenerate_lambda_phage_1kb() -> None:
    """Fetch the first 1000 bp of lambda phage (NC_001416.1) and write FASTA + metadata."""
    accession = "NC_001416.1"
    seq_start, seq_stop = 1, 1000

    print(f"Fetching {accession} bp {seq_start}-{seq_stop} from NCBI Entrez ...")
    handle = Entrez.efetch(
        db="nuccore",
        id=accession,
        rettype="fasta",
        retmode="text",
        seq_start=seq_start,
        seq_stop=seq_stop,
    )
    record = SeqIO.read(handle, "fasta")
    handle.close()

    sequence = str(record.seq).upper()
    if len(sequence) != (seq_stop - seq_start + 1):
        print(
            f"WARNING: expected {seq_stop - seq_start + 1} bp, got {len(sequence)}",
            file=sys.stderr,
        )

    gc_count = sequence.count("G") + sequence.count("C")
    n_count = sequence.count("N")
    # Use the same `ambiguous="remove"` semantic as the gc_content tool so the metadata
    # is directly comparable to tool output for low-N reference sequences like lambda.
    fraction = gc_fraction(sequence, ambiguous="remove")
    gc_percent = round(fraction * 100.0, 6)

    fasta_path = FIXTURE_DIR / "lambda_phage_1kb.fasta"
    meta_path = FIXTURE_DIR / "lambda_phage_1kb.meta.json"

    fasta_path.write_text(f">{record.id} {record.description}\n{sequence}\n")

    meta = {
        "source": "NCBI Entrez efetch",
        "accession": accession,
        "seq_start": seq_start,
        "seq_stop": seq_stop,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "total_length": len(sequence),
        "gc_count": gc_count,
        "gc_percent": gc_percent,
        "n_count": n_count,
        "sha256": hashlib.sha256(sequence.encode("ascii")).hexdigest(),
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    print(f"  wrote {fasta_path.relative_to(FIXTURE_DIR.parent.parent)}")
    print(f"  wrote {meta_path.relative_to(FIXTURE_DIR.parent.parent)}")
    print(f"  total_length={len(sequence)}, gc_count={gc_count}, gc_percent={gc_percent}")


def main() -> None:
    email = _require_email()
    Entrez.email = email
    print(f"Using Entrez email: {email}")
    regenerate_lambda_phage_1kb()
    print("Done. Commit the regenerated fixture + metadata files.")


if __name__ == "__main__":
    main()
