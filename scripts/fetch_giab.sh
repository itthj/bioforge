#!/usr/bin/env bash
# Stage a SMALL, real GIAB-style dataset so the live variant-calling benchmark + the QUAL
# calibration arm produce real numbers in minutes (Limitation #2/#5 data, READINESS section 6).
#
# This fetches the DeepVariant "quickstart" data (~5 MB): a chr20:10-10.01 Mb region of
# NA12878 reads + the matching hg19 chr20 reference + a NIST/GIAB truth VCF + confident BED.
# It is the SAME dataset behind the already-published GIAB number -- a genuine end-to-end run,
# just scoped small so you don't wait on a 3 GB GRCh38 + full HG002 download for the first run.
#
# Usage (WSL2/Linux, with Docker available):
#   bash scripts/fetch_giab.sh            # -> ~/bioforge-data/giab
#   BIOFORGE_GIAB_DIR=/data/giab bash scripts/fetch_giab.sh
#
# Then run the benchmark from Python (see scripts/regenerate_benchmarks.py) with the printed
# env, or just set the printed block in your .env and open the Accuracy Report.
#
# For the FULL genome-wide HG002 / GRCh38 benchmark see READINESS.md section 6 -- same env vars,
# bigger files (reference ~3 GB from the NCBI GIAB FTP, HG002 truth from the GIAB v4.2.1 release).
set -euo pipefail

DIR="${BIOFORGE_GIAB_DIR:-${HOME}/bioforge-data/giab}"
URL="https://storage.googleapis.com/deepvariant/quickstart-testdata.tar.gz"
mkdir -p "${DIR}"
cd "${DIR}"

if [ ! -f quickstart-testdata.tar.gz ]; then
  echo "[giab] Downloading quickstart data (~5 MB)..."
  curl -fsSL "${URL}" -o quickstart-testdata.tar.gz
fi
echo "[giab] Extracting..."
tar -xzf quickstart-testdata.tar.gz

REF="${DIR}/ucsc.hg19.chr20.unittest.fasta"
READS="${DIR}/NA12878_S1.chr20.10_10p1mb.bam"
TRUTH="$(ls "${DIR}"/*.vcf.gz | head -1)"
BED="$(ls "${DIR}"/*.bed | head -1)"

# samtools: prefer a local binary, else the staphb image you already have.
samtools_run() {
  if command -v samtools >/dev/null 2>&1; then samtools "$@";
  else docker run --rm -v "${DIR}:${DIR}" -w "${DIR}" staphb/samtools samtools "$@"; fi
}
[ -f "${REF}.fai" ]   || { echo "[giab] Indexing reference..."; samtools_run faidx "$(basename "${REF}")"; }
[ -f "${READS}.bai" ] || { echo "[giab] Indexing reads...";     samtools_run index "$(basename "${READS}")"; }

cat <<EOF

[giab] Staged in ${DIR}. Add to your .env (the reference BUILD is stated explicitly, never assumed):

  BIOFORGE_DEEPVARIANT_ENABLED=true
  BIOFORGE_DEEPVARIANT_DOCKER_IMAGE=google/deepvariant:1.6.1
  BIOFORGE_GIAB_REFERENCE_PATH=${REF}
  BIOFORGE_GIAB_REFERENCE_BUILD=ucsc.hg19 chr20 (DeepVariant quickstart)
  BIOFORGE_GIAB_READS_PATH=${READS}
  BIOFORGE_GIAB_TRUTH_VCF_PATH=${TRUTH}
  BIOFORGE_GIAB_CONFIDENT_BED_PATH=${BED}
  BIOFORGE_GIAB_REGIONS=chr20:10000000-10010000

Then generate the published artifact (writes ECE/Brier calibration too):
  python scripts/regenerate_benchmarks.py giab \\
    --sample "NA12878 (HG001)" \\
    --truth-set "NIST/GIAB test_nist chr20:10-10.01Mb" \\
    --name "Variant calling: DeepVariant vs NIST/GIAB (quickstart)" \\
    --slug na12878_chr20_quickstart \\
    --interpretation "Small validation region, not a genome-wide HG002 claim."
EOF
