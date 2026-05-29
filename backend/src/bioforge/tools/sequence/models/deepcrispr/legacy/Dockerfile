# DeepCRISPR (Chuai 2018) legacy inference image -- Python 3.6 / TensorFlow 1.x.
# Apache-2.0 upstream. Built once, then invoked out-of-process by BioForge's runner
# (`docker run --rm -i <image> python /opt/deepcrispr/deepcrispr_infer.py ...`).
#
# IMPORTANT (reconcile at build): the upstream README states tensorflow==1.3.0 +
# dm-sonnet==1.9, but sonnet 1.9 generally wants tensorflow >= 1.8. Treat the pins
# below as a starting point -- find a self-consistent set during the env build and
# numeric validation, then capture the resulting image digest
# (`docker inspect --format '{{index .RepoDigests 0}}' <image>`) and set
# BIOFORGE_DEEPCRISPR_DOCKER_IMAGE to that digest-pinned reference for §10 provenance.
FROM python:3.6-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Pin to a real DeepCRISPR commit at build time:  --build-arg DEEPCRISPR_COMMIT=<sha>
ARG DEEPCRISPR_COMMIT=master

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Pinned legacy scientific stack. Reconcile TF/sonnet versions if they conflict.
RUN pip install \
      numpy==1.16.6 \
      scipy==1.2.3 \
      "tensorflow==1.3.0" \
      "dm-sonnet==1.9"

# Upstream DeepCRISPR (Apache-2.0), pinned to a commit for reproducibility.
RUN git clone https://github.com/bm2-lab/DeepCRISPR.git /opt/DeepCRISPR \
    && git -C /opt/DeepCRISPR checkout "${DEEPCRISPR_COMMIT}"
ENV PYTHONPATH=/opt/DeepCRISPR

# BioForge's legacy inference entrypoint (the JSON-protocol script).
COPY deepcrispr_infer.py /opt/deepcrispr/deepcrispr_infer.py

# No ENTRYPOINT: the runner passes the full `python ... --model-dir ...` command.
CMD ["python", "/opt/deepcrispr/deepcrispr_infer.py", "--help"]
