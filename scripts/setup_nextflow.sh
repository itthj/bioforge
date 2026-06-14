#!/usr/bin/env bash
# Install Nextflow (Limitation #5 pipelines) into ~/.local -- no sudo required.
#
# Nextflow is a JVM tool, not a Docker image, so pulling the pipeline containers is not enough.
# This installs a userspace Temurin JDK 17 + the nextflow launcher and verifies `nextflow -version`.
# Run inside WSL2 / Linux:  bash scripts/setup_nextflow.sh
#
# After it finishes, add the printed line to your shell rc (or this shell) and set
# BIOFORGE_NEXTFLOW_ENABLED=true in your .env.
set -euo pipefail

PREFIX="${HOME}/.local"
JDK_DIR="${PREFIX}/jdk17"
BIN_DIR="${PREFIX}/bin"
mkdir -p "${BIN_DIR}"

# --- Java 17 (userspace Temurin) ---
if command -v java >/dev/null 2>&1 && java -version 2>&1 | grep -qE 'version "(17|2[0-9])'; then
  echo "[setup] Java 17+ already present: $(java -version 2>&1 | head -1)"
  JAVA_BIN="$(command -v java)"
elif [ -x "${JDK_DIR}/bin/java" ]; then
  echo "[setup] Reusing JDK at ${JDK_DIR}"
  JAVA_BIN="${JDK_DIR}/bin/java"
else
  echo "[setup] Downloading Temurin JDK 17 (userspace, no sudo)..."
  ARCH="$(uname -m)"; case "${ARCH}" in x86_64) A=x64;; aarch64) A=aarch64;; *) A="${ARCH}";; esac
  URL="https://api.adoptium.net/v3/binary/latest/17/ga/linux/${A}/jdk/hotspot/normal/eclipse"
  tmp="$(mktemp -d)"
  curl -fsSL "${URL}" -o "${tmp}/jdk.tar.gz"
  mkdir -p "${JDK_DIR}"
  tar -xzf "${tmp}/jdk.tar.gz" -C "${JDK_DIR}" --strip-components=1
  rm -rf "${tmp}"
  JAVA_BIN="${JDK_DIR}/bin/java"
  echo "[setup] Installed JDK: $(${JAVA_BIN} -version 2>&1 | head -1)"
fi
export JAVA_HOME="$(dirname "$(dirname "${JAVA_BIN}")")"
export PATH="${JAVA_HOME}/bin:${PATH}"

# --- Nextflow ---
if [ -x "${BIN_DIR}/nextflow" ]; then
  echo "[setup] Reusing nextflow at ${BIN_DIR}/nextflow"
else
  echo "[setup] Installing Nextflow into ${BIN_DIR}..."
  ( cd "${BIN_DIR}" && curl -fsSL https://get.nextflow.io | bash )
  chmod +x "${BIN_DIR}/nextflow"
fi

echo "[setup] Verifying..."
"${BIN_DIR}/nextflow" -version

cat <<EOF

[setup] Done. To use nextflow in this + future shells:

  export JAVA_HOME="${JAVA_HOME}"
  export PATH="${JAVA_HOME}/bin:${BIN_DIR}:\$PATH"

Add those two lines to ~/.bashrc to make them permanent, then set in your BioForge .env:

  BIOFORGE_NEXTFLOW_ENABLED=true

Smoke test (no real data needed -- uses nf-core's bundled test profile):
  BIOFORGE_NEXTFLOW_ENABLED=true nextflow run nf-core/rnaseq -r 3.14.0 -profile test,docker --outdir /tmp/nf_test
EOF
