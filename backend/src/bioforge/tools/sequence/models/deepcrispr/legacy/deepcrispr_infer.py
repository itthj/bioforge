"""DeepCRISPR on-target inference -- LEGACY RUNTIME wrapper (runs inside the DeepCRISPR env).

NOT imported by the BioForge package. Invoked as a subprocess by `runner.py`, speaking JSON:

    stdin :  {"guides": ["<23bp>", ...], "model": "ontar_cnn_reg_seq"}
    stdout:  {"model": "...", "scores": [<float>, ...]}     on success
             {"error": "<message>"}                          on failure (also exit 1)

VALIDATED against `michaelchuai/deepcrispr:latest` (Python 3.6.5, TensorFlow 1.13.2): it
uses DeepCRISPR's OWN encoder (`deepcrispr.Sgt`, via a one-column `.rsgt` with `with_y=False`)
and the bundled seq-only regression weights, so there is no reimplemented encoding and no
chance of divergence. Excluded from the repo's ruff config -- it targets the DeepCRISPR env.

Defaults assume the authors' image layout (repo at /root/DeepCRISPR, weights at
/root/DeepCRISPR/trained_models/ontar_cnn_reg_seq). Override with DEEPCRISPR_REPO_DIR /
DEEPCRISPR_MODEL_DIR or the --repo-dir / --model-dir flags for a local install.
"""

from __future__ import print_function

import argparse
import json
import os
import sys
import tempfile

# TensorFlow warnings and DeepCRISPR's own prints ("... loaded") would otherwise pollute
# stdout and corrupt the JSON protocol. Keep a handle to the real stdout and route ALL other
# library chatter to stderr; only the result JSON is ever written to the real stdout.
_REAL_STDOUT = sys.stdout
sys.stdout = sys.stderr

_DEFAULT_REPO_DIR = os.environ.get("DEEPCRISPR_REPO_DIR", "/root/DeepCRISPR")
_DEFAULT_MODEL_DIR = os.environ.get(
    "DEEPCRISPR_MODEL_DIR", "/root/DeepCRISPR/trained_models/ontar_cnn_reg_seq"
)


def _emit(obj):
    _REAL_STDOUT.write(json.dumps(obj))
    _REAL_STDOUT.flush()


def _fail(message):
    _emit({"error": message})
    sys.stderr.write("deepcrispr_infer: " + message + "\n")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="DeepCRISPR seq-only on-target inference (legacy runtime).")
    parser.add_argument("--model-dir", default=_DEFAULT_MODEL_DIR)
    parser.add_argument("--repo-dir", default=_DEFAULT_REPO_DIR)
    args = parser.parse_args()

    try:
        request = json.loads(sys.stdin.read())
    except ValueError as e:
        _fail("could not parse stdin JSON: %s" % e)
        return

    guides = request.get("guides")
    if not isinstance(guides, list) or not guides:
        _fail("request must carry a non-empty 'guides' list")
        return

    # DeepCRISPR's package + relative model paths resolve from the repo dir.
    if args.repo_dir and args.repo_dir not in sys.path:
        sys.path.insert(0, args.repo_dir)
    if os.path.isdir(args.repo_dir):
        os.chdir(args.repo_dir)

    try:
        import numpy as np
        import tensorflow as tf

        import deepcrispr as dc
    except Exception as e:  # noqa: E722 -- surface any import failure as a clean protocol error
        _fail("failed to import the DeepCRISPR stack (is this the DeepCRISPR env/image?): %s" % e)
        return

    try:
        # Write the 23 bp guides to a one-column .rsgt and let DeepCRISPR's own Sgt encode
        # them (with_y=False -> the single column is the sequence). x -> [N, 4, 1, 23].
        tmp = tempfile.mktemp(suffix=".rsgt")
        with open(tmp, "w") as f:
            f.write("\n".join(g.strip().upper() for g in guides) + "\n")
        x = dc.Sgt(tmp, with_y=False).get_dataset()
        x = np.expand_dims(x, axis=2)

        sess = tf.InteractiveSession()
        try:
            dcmodel = dc.DCModelOntar(sess, args.model_dir, is_reg=True, seq_feature_only=True)
            predicted = dcmodel.ontar_predict(x)
            scores = [float(v) for v in np.asarray(predicted).reshape(-1).tolist()]
        finally:
            sess.close()
    except Exception as e:  # noqa: E722
        _fail("DeepCRISPR inference failed: %s" % e)
        return

    if len(scores) != len(guides):
        _fail("model returned %d scores for %d guides" % (len(scores), len(guides)))
        return

    _emit({"model": request.get("model", "ontar_cnn_reg_seq"), "scores": scores})


if __name__ == "__main__":
    main()
