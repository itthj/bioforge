import type { TraceDetail, TraceSummary } from "../types/traces";

// Lists a project's runs (newest first). Relative URLs ride the Vite /projects + /traces proxy.
export async function listTraces(
  projectId: string,
  opts: { q?: string; limit?: number } = {},
): Promise<TraceSummary[]> {
  const params = new URLSearchParams();
  if (opts.q) params.set("q", opts.q);
  if (opts.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  const res = await fetch(
    `/projects/${encodeURIComponent(projectId)}/traces${qs ? `?${qs}` : ""}`,
  );
  if (!res.ok) throw new Error(`Failed to list runs (HTTP ${res.status})`);
  return res.json() as Promise<TraceSummary[]>;
}

// Loads one full run for read-only re-rendering.
export async function getTrace(traceId: string): Promise<TraceDetail> {
  const res = await fetch(`/traces/${encodeURIComponent(traceId)}`);
  if (!res.ok) throw new Error(`Failed to load run (HTTP ${res.status})`);
  return res.json() as Promise<TraceDetail>;
}
