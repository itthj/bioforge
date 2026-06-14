# Reference GPU endpoint image. For a real GPU, start FROM a CUDA base
# (e.g. nvidia/cuda:12.4.1-runtime-ubuntu22.04 or pytorch/pytorch) and add `torch` so
# run_task() can use the GPU. This minimal version runs the contract on CPU.
FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn
COPY server.py /app/server.py

# Set GPU_API_KEY at run time to require a bearer token.
ENV PORT=8080
EXPOSE 8080
CMD ["python", "server.py"]
