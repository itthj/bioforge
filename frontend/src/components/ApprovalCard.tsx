import { useState } from "react";
import type { AgentDoneEvent, PlanPayload, PlanStep } from "../types/agent";
import { Card } from "./ui/Card";
import { StatusDot } from "./ui/StatusDot";

interface ApprovalCardProps {
  done: AgentDoneEvent;
  /** Approve (optionally with an edited plan) or cancel. editedPlan is omitted when unchanged. */
  onDecision: (approved: boolean, editedPlan?: PlanPayload) => void;
  disabled?: boolean;
}

/**
 * Review-mode approval gate. The user can edit the proposed plan — reword a step, reorder,
 * or drop a step — before approving.
 *
 * HONESTY: the executor is a free-form tool-use loop that treats the plan as GUIDANCE (it may
 * adapt as it sees tool results), not a hard contract. So editing the plan STEERS the run; it
 * does not constrain exactly which tools fire. The copy says so — we never imply hard control.
 *
 * State resets per run because App.tsx keys this card by `done.trace_id`.
 */
export function ApprovalCard({ done, onDecision, disabled }: ApprovalCardProps) {
  const proposed = done.status === "pending_approval" ? done.pending_plan : null;
  const [steps, setSteps] = useState<PlanStep[]>(() => proposed?.steps ?? []);
  const [dirty, setDirty] = useState(false);

  if (done.status !== "pending_approval" || !proposed) return null;

  const touch = () => setDirty(true);

  const setDescription = (i: number, description: string) => {
    setSteps((cur) => cur.map((s, j) => (j === i ? { ...s, description } : s)));
    touch();
  };
  const removeStep = (i: number) => {
    setSteps((cur) => cur.filter((_, j) => j !== i));
    touch();
  };
  const moveStep = (i: number, dir: -1 | 1) => {
    setSteps((cur) => {
      const j = i + dir;
      if (j < 0 || j >= cur.length) return cur;
      const next = [...cur];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
    touch();
  };

  const approve = () => {
    // Renumber idx to the visible order; only send a plan when the user actually changed it.
    const editedPlan: PlanPayload | undefined = dirty
      ? {
          is_trivial: proposed.is_trivial,
          summary: proposed.summary,
          steps: steps.map((s, i) => ({ ...s, idx: i })),
        }
      : undefined;
    onDecision(true, editedPlan);
  };

  const canApprove = steps.length > 0;

  return (
    <Card className="p-4">
      <div className="flex items-center gap-2 text-sm font-semibold text-warn">
        <StatusDot className="text-warn" pulse />
        Approval required
      </div>

      <div className="mt-2 text-xs text-fg-muted">
        The agent planned the steps below. Nothing has run yet — review and (optionally) edit
        the plan, then approve to execute, or cancel.
      </div>

      <div className="mt-3 rounded-md border border-border bg-bg p-3">
        <div className="text-xs italic text-fg-subtle">{proposed.summary}</div>

        {steps.length === 0 ? (
          <div className="mt-2 text-xs text-warn">
            All steps removed — there is nothing to run. Cancel, or restore a step to approve.
          </div>
        ) : (
          <ol className="mt-2 space-y-1.5">
            {steps.map((s, i) => (
              <li key={i} className="flex items-start gap-2">
                <span className="mt-1.5 w-4 shrink-0 text-right text-[11px] text-fg-subtle">
                  {i + 1}.
                </span>
                <div className="min-w-0 flex-1">
                  <input
                    type="text"
                    value={s.description}
                    disabled={disabled}
                    onChange={(e) => setDescription(i, e.target.value)}
                    aria-label={`Step ${i + 1} description`}
                    className="w-full rounded border border-border bg-surface px-2 py-1 text-xs text-fg focus:border-accent focus:outline-none disabled:opacity-50"
                  />
                  {s.expected_tool && (
                    <span className="ml-0.5 font-mono text-[10px] text-accent">
                      [{s.expected_tool}]
                    </span>
                  )}
                </div>
                <div className="flex shrink-0 gap-0.5">
                  <button
                    type="button"
                    disabled={disabled || i === 0}
                    aria-label={`Move step ${i + 1} up`}
                    onClick={() => moveStep(i, -1)}
                    className="rounded px-1 text-xs text-fg-subtle hover:text-fg disabled:opacity-30"
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    disabled={disabled || i === steps.length - 1}
                    aria-label={`Move step ${i + 1} down`}
                    onClick={() => moveStep(i, 1)}
                    className="rounded px-1 text-xs text-fg-subtle hover:text-fg disabled:opacity-30"
                  >
                    ↓
                  </button>
                  <button
                    type="button"
                    disabled={disabled}
                    aria-label={`Delete step ${i + 1}`}
                    onClick={() => removeStep(i)}
                    className="rounded px-1 text-xs text-danger hover:opacity-80 disabled:opacity-30"
                  >
                    ✕
                  </button>
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>

      <div className="mt-2 text-[11px] italic text-fg-subtle">
        Editing steers the agent: the plan is guidance the executor follows but may adapt as it
        sees tool results — it is not a hard constraint on which tools run.
        {dirty && <span className="ml-1 not-italic text-accent">Plan edited.</span>}
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
          disabled={disabled || !canApprove}
          onClick={approve}
          className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-accent-fg shadow-sm transition hover:opacity-90 disabled:opacity-50"
        >
          {dirty ? "Approve edited plan" : "Approve"}
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
