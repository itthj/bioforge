"""FORECasT inference -- LEGACY RUNTIME wrapper (runs inside the FORECasT env/image).

NOT imported by the BioForge package. Invoked as a subprocess by `runner.py`, speaking JSON:

    stdin :  {"requests": [{"sequence": "<target>", "pam_index": <int>}, ...]}
    stdout:  {"results": [{"predictions": {label: frequency, ...}}, ...]}
             {"error": "<message>"}  on failure (also exit 1)

It drives FORECasT's documented CLI (`python FORECasT.py <target> <pam_index> <prefix>`),
then parses the predicted-indel profile into a label->frequency map, emitted verbatim (no
remapping). Excluded from the repo's ruff config -- it targets the FORECasT env.

VERIFY at numeric validation (confirm against the FORECasT version in the image/env):
  * the FORECasT.py entrypoint path -- override with the FORECAST_SCRIPT env var if needed;
  * which output file is the predicted-indel profile and its column layout (label + count).
If these differ, the wrapper raises loudly (a clean protocol error); it never emits a
misparsed distribution.
"""

from __future__ import print_function

import glob
import json
import os
import subprocess
import sys
import tempfile

# VERIFY: the FORECasT entrypoint inside the env/image. Override via FORECAST_SCRIPT.
_FORECAST_SCRIPT = os.environ.get("FORECAST_SCRIPT", "FORECasT.py")


def _fail(message):
    sys.stdout.write(json.dumps({"error": message}))
    sys.stdout.flush()
    sys.stderr.write("forecast_infer: " + message + "\n")
    sys.exit(1)


def _is_number(value):
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _parse_profile_tsv(path):
    """Parse FORECasT's predicted-indel profile TSV into {label: frequency}.

    Heuristic + VERIFY: first column is the indel label, the last numeric column is the
    count/weight; frequencies are counts normalized to sum to 1.
    """
    with open(path) as handle:
        rows = [line.rstrip("\n").split("\t") for line in handle if line.strip()]
    if not rows:
        return {}
    # Drop a header row if the first row has no numeric cell.
    data = rows[1:] if not any(_is_number(c) for c in rows[0]) else rows
    counts = []
    total = 0.0
    for row in data:
        if not row:
            continue
        label = row[0]
        count = None
        for cell in reversed(row):
            if _is_number(cell):
                count = float(cell)
                break
        if count is None:
            continue
        counts.append((label, count))
        total += count
    if total <= 0:
        return {label: 0.0 for label, _ in counts}
    return {label: count / total for label, count in counts}


def main():
    try:
        request = json.loads(sys.stdin.read())
    except ValueError as e:
        _fail("could not parse stdin JSON: %s" % e)
        return

    requests = request.get("requests")
    if not isinstance(requests, list) or not requests:
        _fail("request must carry a non-empty 'requests' list")
        return

    results = []
    for req in requests:
        seq = req.get("sequence")
        pam = req.get("pam_index")
        if not isinstance(seq, str) or not isinstance(pam, int):
            _fail("each request needs 'sequence' (str) and 'pam_index' (int)")
            return
        tmpdir = tempfile.mkdtemp()
        prefix = os.path.join(tmpdir, "fc")
        try:
            subprocess.check_call(
                ["python", _FORECAST_SCRIPT, seq, str(pam), prefix], stdout=sys.stderr, stderr=sys.stderr
            )
        except Exception as e:  # noqa: E722 -- surface as a clean protocol error
            _fail("FORECasT.py failed (verify FORECAST_SCRIPT path): %s" % e)
            return

        candidates = sorted(glob.glob(prefix + "*"))
        profile = next((c for c in candidates if c.endswith((".txt", ".tsv"))), None)
        if profile is None:
            _fail("could not find a FORECasT output profile for prefix %s (got %r)" % (prefix, candidates))
            return
        try:
            predictions = _parse_profile_tsv(profile)
        except Exception as e:  # noqa: E722
            _fail("could not parse FORECasT profile %s: %s" % (profile, e))
            return
        results.append({"predictions": predictions})

    sys.stdout.write(json.dumps({"results": results}))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
