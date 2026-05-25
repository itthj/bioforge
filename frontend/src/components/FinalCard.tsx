import type { AgentDoneEvent } from "../types/agent";

interface FinalCardProps {
  done: AgentDoneEvent;
}

const STATUS_STYLES: Record<string, { label: string; classes: string }> = {
  completed: { label: "Completed", classes: "bg-emerald-100 text-emerald-800" },
  completed_after_replan: {
    label: "Completed (after replan)",
    classes: "bg-emerald-100 text-emerald-800",
  },
  critique_failed: {
    label: "Critique failed — review carefully",
    classes: "bg-amber-100 text-amber-800",
  },
  refused: { label: "Refused", classes: "bg-rose-100 text-rose-800" },
  error: { label: "Error", classes: "bg-rose-100 text-rose-800" },
  iteration_cap: { label: "Iteration cap hit", classes: "bg-amber-100 text-amber-800" },
  cancelled: { label: "Cancelled", classes: "bg-slate-200 text-slate-700" },
  pending_approval: { label: "Awaiting approval", classes: "bg-orange-100 text-orange-800" },
};

export function FinalCard({ done }: FinalCardProps) {
  if (done.status === "pending_approval") return null;

  const style = STATUS_STYLES[done.status] ?? {
    label: done.status,
    classes: "bg-slate-100 text-slate-700",
  };

  return (
    <div className="rounded-lg border border-slate-300 bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <span
          className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium ${style.classes}`}
        >
          {style.label}
        </span>
        {done.usage && (
          <span className="font-mono text-xs text-slate-500">
            {done.usage.input_tokens + done.usage.output_tokens} tok · $
            {done.usage.cost_usd.toFixed(4)}
          </span>
        )}
      </div>
      <div className="mt-3 whitespace-pre-wrap text-sm text-slate-900">
        {done.response_text}
      </div>
      <div className="mt-3 font-mono text-[11px] text-slate-400">
        trace_id: {done.trace_id} · model: {done.model}
      </div>
    </div>
  );
}
