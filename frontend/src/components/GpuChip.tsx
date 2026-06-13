import { useEffect, useState } from "react";
import { fetchGpuStatus, type GpuStatus } from "../api/gpu";

/**
 * Small header indicator of the cloud-GPU execution path (Limitation #3). Honest by design:
 * shows "GPU: off" when no backend is configured (the default), or "GPU: <host>" when an HTTP
 * GPU endpoint is wired. Never implies a GPU exists when one does not.
 */
export function GpuChip() {
  const [status, setStatus] = useState<GpuStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchGpuStatus()
      .then((s) => {
        if (!cancelled) setStatus(s);
      })
      .catch(() => {
        /* informational; stay silent if it can't be fetched */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!status) return null;

  if (!status.configured) {
    return (
      <span className="text-xs text-fg-subtle" title="No cloud-GPU backend configured (BIOFORGE_GPU_BACKEND=none). GPU-requiring work is unavailable, not faked.">
        GPU: off
      </span>
    );
  }

  return (
    <span className="text-xs text-success" title={`Cloud-GPU execution path active via ${status.endpoint_host}`}>
      GPU: {status.endpoint_host}
    </span>
  );
}
