# Reference GPU endpoint (Limitation #3)

A minimal, real implementation of the contract BioForge's `HttpGpuBackend` speaks. Deploy it on
something with a GPU, point BioForge at it, and `POST /gpu/submit` works.

## Run it

```bash
# Local (CPU; proves the wiring end-to-end):
pip install fastapi uvicorn
GPU_API_KEY=choose-a-secret python server.py        # listens on :8080

# Or containerized:
docker build -t bioforge-gpu-endpoint scripts/gpu_server
docker run --rm -p 8080:8080 -e GPU_API_KEY=choose-a-secret bioforge-gpu-endpoint
```

For a **real GPU**, base the image on CUDA + install `torch` (see the Dockerfile comment) and
deploy to a GPU host: a cloud GPU VM, RunPod/Modal/Replicate web endpoint, or a lab workstation.

## Point BioForge at it

```bash
BIOFORGE_GPU_BACKEND=http
BIOFORGE_GPU_ENDPOINT=https://your-host:8080
BIOFORGE_GPU_API_KEY=choose-a-secret        # same value as GPU_API_KEY
```

The header chip flips to `GPU: <host>` and `POST /gpu/submit` dispatches here. With the backend
unset, GPU work refuses honestly (`GPU: off`) — it is never faked.

## Plug your model in

Edit `run_task(task, inputs)` in `server.py`: load weights once at import, dispatch by `task`,
return a JSON-serializable dict (raise to fail the job). The default handler honestly reports
whether a CUDA device is actually visible and echoes the task — it never pretends to run a model.

## Verify

```bash
curl -s localhost:8080/health          # {"status":"ok","gpu":{...}}  -- shows real CUDA visibility
```
