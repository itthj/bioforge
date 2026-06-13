# BioForge readiness checklist

What it takes to go from "the core agent runs" to "a working product with every
capability lit up." Each capability is **opt-in**: with its switch off, BioForge
falls back to a deterministic rule-based path (or refuses honestly) — it never
fabricates a result. So you can light these up one at a time.

Legend: ✅ works now · 🔧 flip a flag · 📦 install something · 📊 stage data · 🧪 your data.

---

## 0. Core — ✅ works with just an API key

Set `ANTHROPIC_API_KEY` (in `.env`). That's the whole requirement for the agent,
all in-process tools, planning/critic, the grounding validator, projects/memory/
provenance, the full UI, auth, uploads, cost controls, the feedback loop, and the
4 published benchmark numbers in the Accuracy Report.

```
cp .env.example .env          # edit ANTHROPIC_API_KEY
docker compose up             # or run backend + vite locally (see README)
```

---

## 1. Deep models — 🔧 flip a flag (you already have the images)

The model images are on disk but **not wired** until you set BOTH the enable flag
AND the image name. Add to `.env`:

```bash
BIOFORGE_DEEPCRISPR_ENABLED=true
BIOFORGE_DEEPCRISPR_DOCKER_IMAGE=bioforge/deepcrispr:legacy

BIOFORGE_AZIMUTH_ENABLED=true
BIOFORGE_AZIMUTH_DOCKER_IMAGE=bioforge/azimuth:legacy

BIOFORGE_LINDEL_ENABLED=true
BIOFORGE_LINDEL_DOCKER_IMAGE=bioforge/lindel:legacy

BIOFORGE_FORECAST_ENABLED=true
BIOFORGE_FORECAST_DOCKER_IMAGE=bioforge/forecast:legacy

BIOFORGE_DEEPVARIANT_ENABLED=true
BIOFORGE_DEEPVARIANT_DOCKER_IMAGE=google/deepvariant:1.6.1
```

**Docker-access caveat.** These tools shell out to `docker run`. If the backend
itself runs inside a container, it cannot launch sibling containers unless you
mount the Docker socket (`-v /var/run/docker.sock:/var/run/docker.sock`) or run
the backend on the host. Running the backend on the host (where your `docker` CLI
works) is the simplest path.

What each unlocks:
- DeepCRISPR / Azimuth → real deep on-target scores alongside the rule-based proxy
  (`score_guide_on_target`).
- Lindel / FORECasT → real per-guide edit-outcome distributions (`edit_outcome`).
- DeepVariant → the GIAB benchmark caller **and** the `call_variants` tool (§3 below).

## 2. MAFFT (multiple-sequence alignment) — 📦 build the image

The MAFFT image is the one that wasn't pre-built. Build the **core-only** image
(the bundled extensions are restrictively licensed — keep them out):

```bash
docker build -t bioforge/mafft:legacy \
  backend/src/bioforge/tools/sequence/models/mafft/legacy
# then:
BIOFORGE_MAFFT_ENABLED=true
BIOFORGE_MAFFT_DOCKER_IMAGE=bioforge/mafft:legacy
```

Or point `BIOFORGE_MAFFT_BINARY` at a local `mafft`. Until then `align_msa`
refuses with setup guidance (no faked alignment).

## 3. Variant calling on your own data — ✅ once DeepVariant is on

`call_variants` runs DeepVariant over an uploaded BAM + reference and returns the
called variants (optionally scored against a truth set). Requires `#1` DeepVariant
flags. Refuses honestly when the flag is off.

## 4. External bio-tool binaries (BLAST, samtools, …) — 📦 in the backend image

The default `Dockerfile.backend` now bundles `ncbi-blast+`, `samtools`, `bcftools`,
`bwa`, `minimap2`, and `primer3`. Rebuild the backend image (`docker compose build
backend`) to pick them up. Running the backend on the host instead? Install those
via your package manager (or conda) so they're on `PATH`. (BLAST also has an NCBI
web fallback, so remote BLAST works even without the local binary.)

## 5. Pipelines (nf-core / Nextflow, #5) — 📦 install Nextflow

Nextflow is a JVM tool, **not** a Docker image — installing the pipeline containers
is not enough. Install it (https://www.nextflow.io/, needs Java 17+) so `nextflow`
is on PATH, then:

```bash
BIOFORGE_NEXTFLOW_ENABLED=true
```

Use `profile=test` for a fast smoke run. For real multi-hour runs switch to durable
jobs (`#7`) so the API isn't blocked.

## 6. Live benchmarks (turn guard_only → live numbers) — 📊 stage data

The Accuracy Report already shows 4 published numbers. To recompute them live:

- **On/off-target + edit-outcome:** consent to the fetch-on-first-use datasets:
  ```bash
  BIOFORGE_CRISPOR_EFFDATA_CONSENT=true
  BIOFORGE_FORECAST_PROFILES_CONSENT=true
  ```
- **GIAB variant calling:** stage the reference + truth set (you supply these; the
  reference BUILD must be stated, never assumed):
  ```bash
  BIOFORGE_GIAB_REFERENCE_PATH=/data/giab/GRCh38.fa
  BIOFORGE_GIAB_REFERENCE_BUILD=GRCh38.p14
  BIOFORGE_GIAB_READS_PATH=/data/giab/HG002.bam
  BIOFORGE_GIAB_TRUTH_VCF_PATH=/data/giab/HG002_truth.vcf.gz
  BIOFORGE_GIAB_CONFIDENT_BED_PATH=/data/giab/HG002_confident.bed
  BIOFORGE_GIAB_REGIONS=chr20
  ```

## 7. Scale: durable jobs — 🔧 compose profile

```bash
docker compose --profile workers up      # redis + a Celery worker + Postgres
# in .env:
BIOFORGE_TASK_QUEUE=celery
BIOFORGE_DB_URL=postgresql+asyncpg://bioforge:bioforge@postgres:5432/bioforge
```

## 8. Cloud GPU (#3) — 📦 provision an endpoint

```bash
BIOFORGE_GPU_BACKEND=http
BIOFORGE_GPU_ENDPOINT=https://your-gpu-service.example.com
BIOFORGE_GPU_API_KEY=...
```

Any HTTP service implementing the two-route contract in `gpu/backend.py` works
(self-hosted, Modal, RunPod, Replicate). With `none`, GPU work refuses honestly.

## 9. Wet-lab feedback loop (#4) — 🧪 your data

The loop computes agreement/calibration honestly over whatever you put in. Record
predictions (`POST /predictions`), then measured outcomes (`/outcome` or bulk), then
view agreement in the **Feedback** tab. No external setup — it just needs real results.

---

## Productionizing (exposing to others)

- `BIOFORGE_CORS_ORIGINS=https://your-frontend.example.com` (default is localhost only).
- Terminate TLS at a reverse proxy; give the API a real origin.
- Use the `workers` + `storage` profiles (Postgres + MinIO) and back them up.
- Keep secrets (`ANTHROPIC_API_KEY`, GPU key) in a secret store, not the image.

## The honesty contract (why some things "need data, not a switch")

Benchmarks with live numbers need real genomes/truth-sets; the feedback loop needs
real wet-lab outcomes. These are inputs, not toggles, **by design** — BioForge never
fabricates accuracy or biology, and a capability that cannot run says so rather than
inventing an answer.
