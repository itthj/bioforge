"""DeepCRISPR on-target inference — LEGACY RUNTIME script (Python 3.6 / TF 1.3).

This file is NOT imported by the BioForge package. It runs INSIDE the pinned legacy
environment (the Docker image or the conda env), invoked as a subprocess by
`runner.py`. It speaks the JSON protocol:

    stdin :  {"guides": ["<23bp>", ...], "model": "ontar_cnn_reg_seq"}
    stdout:  {"model": "...", "scores": [<float>, ...]}     on success
             {"error": "<message>"}                          on failure (also exit 1)

Keep this file Python-3.6 compatible (no PEP 604 unions, walrus, or match). It is
excluded from the repo's ruff config because it targets a different interpreter.

VERIFY at numeric validation: the one-hot encoding below (A,C,G,T -> 4 channels,
shape [N,4,1,23]) must match what DeepCRISPR was trained with. If the upstream repo
exposes its own sequence encoder, prefer importing and using that to remove any
chance of channel-order divergence. The score values' range/normalization should
also be confirmed against the paper before they are surfaced as calibrated numbers.
"""

from __future__ import print_function

import argparse
import glob
import json
import os
import sys

GUIDE_LENGTH_BP = 23
# Channel order assumed by the one-hot fallback. VERIFY against the upstream encoder.
_BASE_TO_CHANNEL = {"A": 0, "C": 1, "G": 2, "T": 3}


def _fail(message):
    """Emit a JSON error on stdout, a human message on stderr, and exit nonzero."""
    sys.stdout.write(json.dumps({"error": message}))
    sys.stdout.flush()
    sys.stderr.write("deepcrispr_infer: " + message + "\n")
    sys.exit(1)


def _resolve_model_dir(model_dir):
    """Find the directory that actually holds the TF checkpoint.

    The weights tarball may extract to a nested folder. Descend until we find a
    directory containing a `checkpoint` file or `*.meta` / `*.ckpt*` files.
    """
    candidates = [model_dir]
    for _ in range(4):  # bounded descent
        new_candidates = []
        for d in candidates:
            if not os.path.isdir(d):
                continue
            has_ckpt = (
                os.path.exists(os.path.join(d, "checkpoint"))
                or glob.glob(os.path.join(d, "*.meta"))
                or glob.glob(os.path.join(d, "*.ckpt*"))
            )
            if has_ckpt:
                return d
            for name in sorted(os.listdir(d)):
                sub = os.path.join(d, name)
                if os.path.isdir(sub):
                    new_candidates.append(sub)
        if not new_candidates:
            break
        candidates = new_candidates
    return model_dir  # let DCModelOntar raise a clear error if this is wrong


def _encode(guide):
    """One-hot encode a 23 bp guide into a [4, 1, 23] float array.

    VERIFY: confirm this matches DeepCRISPR's training encoding (channel order +
    layout). If the repo ships an encoder, use it instead of this fallback.
    """
    import numpy as np

    guide = guide.strip().upper()
    if len(guide) != GUIDE_LENGTH_BP:
        raise ValueError("guide must be %d bp, got %d" % (GUIDE_LENGTH_BP, len(guide)))
    arr = np.zeros((4, 1, GUIDE_LENGTH_BP), dtype=np.float32)
    for pos, base in enumerate(guide):
        if base not in _BASE_TO_CHANNEL:
            raise ValueError("non-ACGT base %r in guide" % base)
        arr[_BASE_TO_CHANNEL[base], 0, pos] = 1.0
    return arr


def main():
    parser = argparse.ArgumentParser(description="DeepCRISPR seq-only on-target inference (legacy runtime).")
    parser.add_argument("--model-dir", required=True, help="Directory with the extracted on-target model.")
    args = parser.parse_args()

    try:
        request = json.loads(sys.stdin.read())
    except ValueError as e:
        _fail("could not parse stdin JSON: %s" % e)
        return

    guides = request.get("guides")
    model = request.get("model", "ontar_cnn_reg_seq")
    if not isinstance(guides, list) or not guides:
        _fail("request must carry a non-empty 'guides' list")
        return

    try:
        import numpy as np
        import tensorflow as tf

        from deepcrispr import DCModelOntar
    except Exception as e:  # noqa: E722 — surface any import failure as a clean protocol error
        _fail(
            "failed to import the legacy stack (tensorflow / deepcrispr): %s. "
            "Is this running inside the pinned legacy environment?" % e
        )
        return

    try:
        x = np.stack([_encode(g) for g in guides], axis=0)  # [N, 4, 1, 23]
        model_dir = _resolve_model_dir(args.model_dir)

        sess = tf.InteractiveSession()
        try:
            # is_reg=True (regression), seq_feature_only=True (4-channel seq-only model).
            dcmodel = DCModelOntar(sess, model_dir, True, True)
            predicted = dcmodel.ontar_predict(x)
            scores = [float(v) for v in np.asarray(predicted).reshape(-1).tolist()]
        finally:
            sess.close()
    except Exception as e:  # noqa: E722 — protocol boundary: never leak a traceback to stdout
        _fail("inference failed: %s" % e)
        return

    if len(scores) != len(guides):
        _fail("model returned %d scores for %d guides" % (len(scores), len(guides)))
        return

    sys.stdout.write(json.dumps({"model": model, "scores": scores}))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
