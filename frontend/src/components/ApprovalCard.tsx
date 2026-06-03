import type { AgentDoneEvent } from "../types/agent";
import { Card } from "./ui/Card";
import { StatusDot } from "./ui/StatusDot";

interface ApprovalCardProps {
  done: AgentDoneEvent;
  onDecision: (approved: boolean) => void;
  disabled?: boolean;
}

export function ApprovalCard({ done, onDecision, disabled }: ApprovalCardProps) {
  if (done.status !== "pending_approval" || !done.pending_plan) return null;

  return (
    <Card className="p-4">
      <div className="flex items-center gap-2 text-sm font-semibold text-warn">
        <StatusDot className="text-warn" pulse />
        Approval required
      </div>

      <div className="mt-2 text-xs text-fg-muted">
        The agent planned the steps below. Nothing has run yet — approve to
        execute, or cancel. See the reasons for this checkpoint below.
      </div>

      <div className="mt-3 rounded-md border border-border bg-bg p-3">
        <div className="text-xs italic text-fg-subtle">
          {done.pending_plan.summary}
        </div>
        <ol className="mt-2 list-decimal space-y-1 pl-5 text-xs text-fg-muted">
          {done.pending_plan.steps.map((s) => (
            <li key={s.idx}>
              <span className="font-medium text-fg">{s.description}</span>
              {s.expected_tool && (
                <span className="ml-2 font-mono text-accent">[{s.expected_tool}]</span>
              )}
            </li>
          ))}
        </ol>
      </div>

      {done.approval_reasons.length > 0 && (
        <div className="mt-3">
          <div className="text-xs font-semibold text-warn">Reasons:</div>
          <ul className="ml-4 list-disc text-xs text-fg-muted">
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
          className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-accent-fg shadow-sm transition hover:opacity-90 disabled:opacity-50"
        >
          Approve
        </button>
        <button
          type="button"
          disabled={disabled}
          onClick={() => onDecision(false)}
          className="rounded-md border border-border bg-surface-2 px-4 py-1.5 text-sm font-medium text-fg-muted shadow-sm transition hover:text-fg disabled:opacity-50"
        >
          Cancel
        </button>
      </div>
    </Card>
  );
}
