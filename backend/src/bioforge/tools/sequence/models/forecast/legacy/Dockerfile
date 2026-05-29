# Optional thin image for FORECasT (Allen 2018), built FROM the authors' official image so
# the JSON wrapper is baked in (digest-pinnable for §10 provenance). The default runner
# instead bind-mounts the wrapper into the official image, so this is only needed if you
# prefer a self-contained pinned image.
#
#   docker build -t bioforge/forecast:legacy .
#   docker inspect --format '{{index .RepoDigests 0}}' bioforge/forecast:legacy
# then set BIOFORGE_FORECAST_DOCKER_IMAGE to that digest (and the runner mount is harmless).
FROM quay.io/felicityallen/selftarget

COPY forecast_infer.py /opt/forecast/forecast_infer.py

# VERIFY: set FORECAST_SCRIPT to the absolute path of FORECasT.py inside this image if it is
# not resolvable on PATH (the wrapper defaults to "FORECasT.py").
# ENV FORECAST_SCRIPT=/SelfTarget/.../FORECasT.py

CMD ["python", "/opt/forecast/forecast_infer.py"]
