"""Lindel edit-outcome inference -- LEGACY RUNTIME wrapper (runs inside the Lindel env).

NOT imported by the BioForge package. Invoked as a subprocess by `runner.py`, speaking JSON:

    stdin :  {"sequences": ["<60bp>", ...]}
    stdout:  {"results": [{"frameshift_ratio": <float>, "predictions": {label: freq, ...}}, ...]}
             {"error": "<message>"}  on failure (also exit 1)

It uses Lindel's OWN `gen_prediction` and bundled weights, and emits the label->frequency
map verbatim (no remapping). Excluded from the repo's ruff config — it targets the Lindel
env, not the modern interpreter.

VERIFY at numeric validation (these mirror Lindel_prediction.py and must be confirmed
against the pinned upstream commit): the weight/prereq filenames + their location in the
Lindel package dir, and how the rev_index (array-position -> outcome label) is stored inside
`model_prereq.pkl`. If any differ, this wrapper raises loudly (a clean protocol error), it
never emits a misdecoded distribution.
"""

from __future__ import print_function

import json
import os
import pickle
import sys

# Library chatter (Lindel / numpy prints) must not pollute the JSON protocol on stdout. Keep
# a handle to the real stdout and route everything else to stderr; only the result JSON is
# written to the real stdout.
_REAL_STDOUT = sys.stdout
sys.stdout = sys.stderr


def _emit(obj):
    _REAL_STDOUT.write(json.dumps(obj))
    _REAL_STDOUT.flush()


def _fail(message):
    _emit({"error": message})
    sys.stderr.write("lindel_infer: " + message + "\n")
    sys.exit(1)


def _extract_rev_index(prereq):
    """Locate Lindel's array-position -> outcome-label mapping inside the prereq object.

    VERIFY against Lindel_prediction.py: the exact container/key may differ by commit.
    """
    if isinstance(prereq, dict) and "rev_index" in prereq:
        return prereq["rev_index"]
    if isinstance(prereq, (list, tuple)):
        for item in prereq:
            if isinstance(item, dict) and item and all(isinstance(k, int) for k in item):
                return item
    raise ValueError("could not locate rev_index in model_prereq.pkl (verify the prereq structure)")


def _to_label_map(y_hat, rev_index):
    out = {}
    for i, freq in enumerate(list(y_hat)):
        label = rev_index.get(i) if isinstance(rev_index, dict) else (rev_index[i] if i < len(rev_index) else None)
        if label is None:
            continue
        out[str(label)] = float(freq)
    return out


def main():
    try:
        request = json.loads(sys.stdin.read())
    except ValueError as e:
        _fail("could not parse stdin JSON: %s" % e)
        return

    sequences = request.get("sequences")
    if not isinstance(sequences, list) or not sequences:
        _fail("request must carry a non-empty 'sequences' list")
        return

    try:
        import Lindel
        from Lindel.Predictor import gen_prediction
    except Exception as e:  # noqa: E722 -- surface any import failure as a clean protocol error
        _fail("failed to import Lindel (is this running inside the Lindel env?): %s" % e)
        return

    try:
        lindel_path = Lindel.__path__[0]
        with open(os.path.join(lindel_path, "Model_weights.pkl"), "rb") as f:
            weights = pickle.load(f)
        with open(os.path.join(lindel_path, "model_prereq.pkl"), "rb") as f:
            prereq = pickle.load(f)
        rev_index = _extract_rev_index(prereq)
    except Exception as e:  # noqa: E722
        _fail("could not load Lindel weights/prereq: %s" % e)
        return

    results = []
    for seq in sequences:
        try:
            y_hat, fs = gen_prediction(seq, weights, prereq)
        except Exception as e:  # noqa: E722
            _fail("Lindel gen_prediction failed for a sequence: %s" % e)
            return
        results.append({"frameshift_ratio": float(fs), "predictions": _to_label_map(y_hat, rev_index)})

    _emit({"results": results})


if __name__ == "__main__":
    main()
