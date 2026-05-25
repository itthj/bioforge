import type { AgentDoneEvent } from "../types/agent";

interface ApprovalCardProps {
  done: AgentDoneEvent;
  onDecision: (approved: boolean) => void;
  disabled?: boolean;
}

export function ApprovalCard({ done, onDecision, disabled }: ApprovalCardProps) {
  if (done.status !== "pending_approval" || !done.pending_plan) return null;

  return (
    <div className="rounded-lg border border-orange-300 bg-orange-50 p-4 shadow-sm">
      <div className="flex items-center gap-2 text-sm font-semibold text-orange-900">
        <span className="inline-block h-2 w-2 rounded-full bg-orange-500" />
        Approval required
      </div>

      <div className="mt-2 text-xs text-orange-900">
        The agent built a plan that includes an expensive or destructive step.
        Review and approve before it runs.
      </div>

      <div className="mt-3 rounded border border-orange-200 bg-white p-3">
        <div className="text-xs italic text-slate-600">
          {done.pending_plan.summary}
        </div>
        <ol className="mt-2 list-decimal space-y-1 pl-5 text-xs">
          {done.pending_plan.steps.map((s) => (
            <li key={s.idx}>
              <span className="font-medium">{s.description}</span>
              {s.expected_tool && (
                <span className="ml-2 font-mono text-emerald-700">
                  [{s.expected_tool}]
                </span>
              )}
            </li>
          ))}
        </ol>
      </div>

      {done.approval_reasons.length > 0 && (
        <div className="mt-3">
          <div className="text-xs font-semibold text-orange-900">Reasons:</div>
          <ul className="ml-4 list-disc text-xs text-orange-900">
            {done.approval_reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-4 flex gap-2">
        <button
          type="button"
          disabled={disabled}
          onClick={() => onDecision(true)}
          className="rounded-md bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-emerald-700 disabled:bg-slate-400"
        >
          Approve
        </button>
        <button
          type="button"
          disabled={disabled}
          onClick={() => onDecision(false)}
          className="rounded-md border border-slate-300 bg-white px-4 py-1.5 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:bg-slate-100"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
