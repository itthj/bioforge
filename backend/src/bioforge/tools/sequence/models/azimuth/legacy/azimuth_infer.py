"""Azimuth / Doench Rule Set 2 on-target inference -- LEGACY RUNTIME wrapper.

NOT imported by the BioForge package. Invoked as a subprocess by `runner.py`, speaking JSON:

    stdin :  {"thirtymers": ["<30nt>", ...], "model": "V3_model_nopos"}
    stdout:  {"model": "...", "scores": [<float>, ...]}     on success
             {"error": "<message>"}                          on failure (also exit 1)

SCAFFOLD -- NOT yet validated against a built image. The Azimuth `predict()` call below follows
the documented API of MicrosoftResearch/Azimuth (and the Biomatters/Azimuth py3 port), but the
exact signature AND how the no-position model (`V3_model_nopos`) is selected MUST be VERIFIED
against the upstream README the first time the legacy image is built. Until then this script is
the *contract*, not a validated path. It uses Azimuth's OWN featurizer + the committed pickles
-- no reimplemented encoding -- so once the call is confirmed there is no risk of divergence
from the published model. Excluded from the repo's ruff config (targets the Azimuth env).

Attribution: Azimuth and its committed weights are BSD-3-Clause (c) 2015 Microsoft Research
(docs/license_audit.md); cite Doench et al., Nat Biotechnol 2016.
"""

from __future__ import print_function

import json
import sys

# Azimuth / scikit-learn chatter ("... loaded", convergence notes) would otherwise pollute
# stdout and corrupt the JSON protocol. Keep a handle to the real stdout and route all other
# library output to stderr; only the result JSON is ever written to the real stdout.
_REAL_STDOUT = sys.stdout
sys.stdout = sys.stderr


def _emit(obj):
    _REAL_STDOUT.write(json.dumps(obj))
    _REAL_STDOUT.flush()


def _fail(message):
    _emit({"error": message})
    sys.stderr.write("azimuth_infer: " + message + "\n")
    sys.exit(1)


def main():
    try:
        request = json.loads(sys.stdin.read())
    except ValueError as e:
        _fail("could not parse stdin JSON: %s" % e)
        return

    thirtymers = request.get("thirtymers")
    if not isinstance(thirtymers, list) or not thirtymers:
        _fail("request must carry a non-empty 'thirtymers' list")
        return
    model = request.get("model", "V3_model_nopos")

    if model == "V3_model_full":
        _fail(
            "V3_model_full needs aa_cut + percent_peptide (cut-site context) which this "
            "guide-only path does not provide; use V3_model_nopos."
        )
        return

    try:
        import numpy as np

        import azimuth.model_comparison as azi
    except Exception as e:  # noqa: E722 -- surface any import failure as a clean protocol error
        _fail("failed to import the Azimuth stack (is this the Azimuth env/image?): %s" % e)
        return

    try:
        seqs = np.array([t.strip().upper() for t in thirtymers])
        # Sequence-only (V3_model_nopos): pass no cut-site context so Azimuth selects the
        # no-position model. VERIFY this selection + signature against the Azimuth README at
        # image-build time -- some versions take aa_cut/percent_peptide=None, others omit them.
        predictions = azi.predict(seqs, aa_cut=None, percent_peptide=None)
        scores = [float(v) for v in np.asarray(predictions).reshape(-1).tolist()]
    except Exception as e:  # noqa: E722
        _fail("Azimuth inference failed: %s" % e)
        return

    if len(scores) != len(thirtymers):
        _fail("model returned %d scores for %d guides" % (len(scores), len(thirtymers)))
        return

    _emit({"model": model, "scores": scores})


if __name__ == "__main__":
    main()
