import { useEffect, useRef, useState } from "react";
import type { TraceSummary } from "../types/traces";
import { listTraces } from "../api/traces";
import { cn } from "../lib/cn";
import { StatusDot } from "./ui/StatusDot";

// Status → dot color, mirroring the trace timeline.
const STATUS_DOT: Record<string, string> = {
  completed: "text-success",
  completed_after_replan: "text-success",
  critique_failed: "text-warn",
  pending_approval: "text-warn",
  iteration_cap: "text-warn",
  refused: "text-danger",
  error: "text-danger",
  cancelled: "text-fg-muted",
};

function fmtTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

interface RunHistoryProps {
  projectId: string;
  onOpen: (traceId: string) => void;
}

export function RunHistory({ projectId, onOpen }: RunHistoryProps) {
  const [runs, setRuns] = useState<TraceSummary[] | null>(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const debounced = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (debounced.current) clearTimeout(debounced.current);
    debounced.current = setTimeout(() => {
      setError(null);
      setRuns(null);
      listTraces(projectId, { q: query.trim() || undefined, limit: 100 })
        .then((r) => !cancelled && setRuns(r))
        .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    }, 250);
    return () => {
      cancelled = true;
      if (debounced.current) clearTimeout(debounced.current);
    };
  }, [projectId, query]);

  return (
    <div className="space-y-3">
      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search runs by goal…"
        className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-fg shadow-sm placeholder:text-fg-subtle focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
      />

      {error && (
        <div className="rounded-md border border-danger bg-surface p-3 text-sm text-danger">
          {error}
        </div>
      )}

      {runs === null && !error && (
        <div className="text-sm text-fg-subtle">Loading runs…</div>
      )}

      {runs && runs.length === 0 && (
        <div className="rounded-lg border border-dashed border-border bg-surface p-6 text-center text-sm text-fg-subtle">
          {query.trim()
            ? "No runs match that search."
            : "No runs yet — type a goal in the Chat tab to create one."}
        </div>
      )}

      {runs && runs.length > 0 && (
        <ul className="space-y-2">
          {runs.map((r) => (
            <li key={r.trace_id}>
              <button
                type="button"
                onClick={() => onOpen(r.trace_id)}
                className="flex w-full items-start gap-2.5 rounded-lg border border-border bg-surface p-3 text-left transition-colors hover:bg-surface-2"
              >
                <StatusDot className={cn("mt-1.5", STATUS_DOT[r.status] ?? "text-fg-muted")} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-fg">{r.goal}</div>
                  {r.response_preview && (
                    <div className="mt-0.5 line-clamp-2 text-xs text-fg-muted">
                      {r.response_preview}
                    </div>
                  )}
                  <div className="mt-1 font-mono text-[10px] text-fg-subtle">
                    {fmtTime(r.created_at)} · {r.status} · ${r.cost_usd.toFixed(4)}
                  </div>
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
