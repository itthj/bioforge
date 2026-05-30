"""FORECasT inference -- LEGACY RUNTIME wrapper (runs inside the FORECasT env/image).

NOT imported by the BioForge package. Invoked as a subprocess by `runner.py`, speaking JSON:

    stdin :  {"requests": [{"sequence": "<target>", "pam_index": <int>}, ...]}
    stdout:  {"results": [{"predictions": {label: frequency, ...}}, ...]}
             {"error": "<message>"}  on failure (also exit 1)

It drives FORECasT's documented CLI (`python FORECasT.py <target> <pam_index> <prefix>`),
then parses the predicted-indel profile into a label->frequency map, emitted verbatim (no
remapping). Excluded from the repo's ruff config -- it targets the FORECasT env.

VALIDATED 2026-05-29 against quay.io/felicityallen/selftarget (FORECasT, Python 3.6):
  * entrypoint = /app/indel_prediction/predictor/FORECasT.py; single mode is
    `python FORECasT.py <seq> <pam_index> <prefix>`. It is run with cwd = the script's own
    dir so FORECasT's RELATIVE DEFAULT_MODEL theta file resolves; INDELGENTARGET_EXE (the
    compiled indelmap binary) is provided by the image env.
  * the predicted-indel profile is `<prefix>_predictedindelsummary.txt`, lines
    `<indel_label>\t-\t<count>`. We take the first column as the label and the last numeric
    column as the count, then normalize counts to sum to 1.0 -- DROPPING the injected `-`
    null line (a fixed 1000 placeholder = the wild-type reference read for plotting, NOT a
    model prediction) and any `@@@` bulk-mode header. Labels are emitted verbatim.
The wrapper raises loudly on any structural surprise; it never emits a misparsed distribution.
"""

from __future__ import print_function

import glob
import json
import os
import subprocess
import sys
import tempfile

# Library chatter (FORECasT prints) must not pollute the JSON protocol on stdout. Keep a
# handle to the real stdout and route everything else to stderr; only the result JSON is
# written to the real stdout.
_REAL_STDOUT = sys.stdout
sys.stdout = sys.stderr

# The FORECasT entrypoint inside the official image. Its dir also holds the relative theta
# model file, so the wrapper runs the script from there. Override via FORECAST_SCRIPT.
_FORECAST_SCRIPT = os.environ.get("FORECAST_SCRIPT", "/app/indel_prediction/predictor/FORECasT.py")


def _emit(obj):
    _REAL_STDOUT.write(json.dumps(obj))
    _REAL_STDOUT.flush()


def _fail(message):
    _emit({"error": message})
    sys.stderr.write("forecast_infer: " + message + "\n")
    sys.exit(1)


def _is_number(value):
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _parse_profile_tsv(path):
    """Parse FORECasT's <prefix>_predictedindelsummary.txt into {indel_label: frequency}.

    VALIDATED layout: each line is `<indel_label>\t-\t<count>`. First column is the label,
    the last numeric column is the count; frequencies are counts normalized to sum to 1. We
    DROP the injected `-` null line (fixed 1000 placeholder = wild-type reference read, not a
    prediction) and any `@@@`-prefixed bulk-mode id header. Labels are emitted verbatim.
    """
    with open(path) as handle:
        rows = [line.rstrip("\n").split("\t") for line in handle if line.strip()]
    counts = []
    total = 0.0
    for row in rows:
        if not row:
            continue
        label = row[0]
        if label == "-" or label.startswith("@@@"):
            continue  # null placeholder / bulk-mode id header -- not a predicted indel
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

    script_dir = os.path.dirname(_FORECAST_SCRIPT) or None

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
            # Run from the script's dir so FORECasT's relative DEFAULT_MODEL theta resolves.
            subprocess.check_call(
                ["python", _FORECAST_SCRIPT, seq, str(pam), prefix],
                stdout=sys.stderr,
                stderr=sys.stderr,
                cwd=script_dir,
            )
        except Exception as e:  # noqa: E722 -- surface as a clean protocol error
            _fail("FORECasT.py failed (verify FORECAST_SCRIPT path / cwd / INDELGENTARGET_EXE): %s" % e)
            return

        # predictMutationsSingle writes <prefix>_predictedindelsummary.txt (the profile) and
        # <prefix>_predictedreads.txt (representative reads). We want the profile summary.
        profile = prefix + "_predictedindelsummary.txt"
        if not os.path.isfile(profile):
            candidates = sorted(glob.glob(prefix + "*"))
            profile = next((c for c in candidates if c.endswith("_predictedindelsummary.txt")), None)
        if not profile or not os.path.isfile(profile):
            _fail(
                "could not find FORECasT's _predictedindelsummary.txt for prefix %s (got %r)"
                % (prefix, sorted(glob.glob(prefix + "*")))
            )
            return
        try:
            predictions = _parse_profile_tsv(profile)
        except Exception as e:  # noqa: E722
            _fail("could not parse FORECasT profile %s: %s" % (profile, e))
            return
        results.append({"predictions": predictions})

    _emit({"results": results})


if __name__ == "__main__":
    main()
