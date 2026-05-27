"""Task-queue infrastructure for offloading expensive tool calls.

Phase 1's "expensive tools" — BLAST against NCBI, AlphaFold/RCSB structure
fetches, anything that takes > a few seconds — block the agent's event loop
when run inline. This package provides a TaskQueue abstraction so those tools
can be routed through a Celery worker pool when the deployment grows beyond
single-user local.

Implementations:
  - InlineTaskQueue (default): runs tasks synchronously in the calling
    process. Zero infrastructure dependency. Identical behavior to the
    pre-task-queue tool path.
  - CeleryTaskQueue: enqueues tasks against a Redis-backed Celery worker
    pool. Activated by setting BIOFORGE_TASK_QUEUE=celery in the env.

The Celery app + task definitions live in `celery_app.py`. The agent loop
gets the configured queue from `get_task_queue()`. Existing tools that
DON'T need offloading (everything cheap — gc_content, reverse_complement,
parse_vcf, microhomology, etc.) bypass the queue entirely.
"""

from bioforge.tasks.queue import (
    CeleryTaskQueue,
    InlineTaskQueue,
    TaskHandle,
    TaskQueue,
    TaskStatus,
    get_task_queue,
)

__all__ = [
    "CeleryTaskQueue",
    "InlineTaskQueue",
    "TaskHandle",
    "TaskQueue",
    "TaskStatus",
    "get_task_queue",
]
