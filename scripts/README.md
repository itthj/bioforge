# BioForge setup / ops kit

Helpers that collapse the manual "make it a product" steps into one command each. Pair with
[`docs/READINESS.md`](../docs/READINESS.md) (the conceptual checklist). Run from the repo root.

| Step | Script | What it does |
|------|--------|--------------|
| Engage the deep models | [`models.env`](models.env) | Ready-to-paste `.env` lines for the images you already pulled (deepcrispr/azimuth/lindel/forecast/deepvariant + mafft). Append the ones you want, restart the backend. |
| Pipelines (#5) | [`setup_nextflow.sh`](setup_nextflow.sh) | Installs a userspace JDK 17 + Nextflow (no sudo), verifies `nextflow -version`. Then set `BIOFORGE_NEXTFLOW_ENABLED=true`. |
| Live variant benchmark | [`fetch_giab.sh`](fetch_giab.sh) | Stages the small DeepVariant-quickstart GIAB data (~5 MB) + indexes it + prints the exact `.env` block. A real end-to-end run in minutes. |
| Publish benchmark numbers | [`regenerate_benchmarks.py`](regenerate_benchmarks.py) | Runs one benchmark for real (giab / on-target / off-target / edit-outcome) and writes its artifact; the Accuracy Report then serves it. |
| Cloud GPU (#3) | [`gpu_server/`](gpu_server) | A deployable reference GPU endpoint implementing the `HttpGpuBackend` contract. Run it on a GPU host, set `BIOFORGE_GPU_BACKEND=http`. |
| Wet-lab loop (#4) | [`load_outcomes.py`](load_outcomes.py) | Bulk-loads a CSV of predictions + measured outcomes into the feedback loop via the API. |

## Suggested order

1. `cp .env.example .env` and set `ANTHROPIC_API_KEY`. Append the [`models.env`](models.env) lines.
2. `docker compose up` (or run backend on the host so it can `docker run` the model images).
3. Pipelines: `bash scripts/setup_nextflow.sh`, then `BIOFORGE_NEXTFLOW_ENABLED=true`.
4. Benchmarks: `bash scripts/fetch_giab.sh`, paste its `.env` block, then
   `python scripts/regenerate_benchmarks.py giab ...` (the script prints the full command).
5. GPU (optional): deploy `gpu_server/`, set the three `BIOFORGE_GPU_*` vars.
6. Feedback loop: `python scripts/load_outcomes.py your_results.csv`.

Everything is opt-in: skip any step and that capability simply falls back or refuses honestly.
