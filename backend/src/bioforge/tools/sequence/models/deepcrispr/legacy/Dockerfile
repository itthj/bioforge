# DeepCRISPR legacy inference image -- a thin layer over the AUTHORS' official image, which
# already ships a working Python 3.6.5 / TensorFlow 1.13.2 stack with DeepCRISPR and the
# trained weights at /root/DeepCRISPR. This sidesteps the fragile from-scratch TF1/Sonnet
# build entirely. (The README's "tensorflow==1.3.0" is loose; the working image uses 1.13.2.)
#
#   docker build -t bioforge/deepcrispr:legacy .
#   docker inspect --format '{{index .RepoDigests 0}}' bioforge/deepcrispr:legacy
# then set BIOFORGE_DEEPCRISPR_DOCKER_IMAGE to that digest for §10 provenance.
#
# Base image pinned by digest (rule 19 / section 10; resolved + validated 2026-05-29). NEVER
# :latest. Re-resolve with: docker buildx imagetools inspect michaelchuai/deepcrispr:latest
FROM michaelchuai/deepcrispr@sha256:812deef95aac01ca1b3b07363613c6393bdb74460399c27108e2acfe5e559ad2

# In-image layout (used by the wrapper's defaults).
ENV DEEPCRISPR_REPO_DIR=/root/DeepCRISPR \
    DEEPCRISPR_MODEL_DIR=/root/DeepCRISPR/trained_models/ontar_cnn_reg_seq \
    TF_CPP_MIN_LOG_LEVEL=3

# BioForge's legacy inference entrypoint (the JSON-protocol wrapper).
COPY deepcrispr_infer.py /opt/deepcrispr/deepcrispr_infer.py

# Clear the base image's entrypoint so the runner's explicit `python ...` command runs directly.
ENTRYPOINT []
CMD ["python", "/opt/deepcrispr/deepcrispr_infer.py"]
