import type { AgentDoneEvent, AgentStatus, ValidationVerdict } from "../types/agent";
import type { TraceDetail } from "../types/traces";
import { FinalCard } from "./FinalCard";
import { TraceView } from "./TraceView";

// Re-shape a persisted run into the AgentDoneEvent the live components already render, so a
// past run reuses the exact same trace timeline + grounded result + provenance links.
function toDone(t: TraceDetail): AgentDoneEvent {
  return {
    trace_id: t.id,
    status: t.status as AgentStatus,
    response_text: t.response_text,
    model: t.model,
    usage: {
      input_tokens: t.tokens_input,
      output_tokens: t.tokens_output,
      cache_creation_tokens: t.tokens_cache_creation,
      cache_read_tokens: t.tokens_cache_read,
      cost_usd: t.cost_usd,
      model: t.model,
    },
    pending_plan: (t.awaiting_approval_plan as AgentDoneEvent["pending_plan"]) ?? null,
    approval_reasons: t.approval_reasons ?? [],
  };
}

interface RunDetailProps {
  trace: TraceDetail;
  onBack: () => void;
}

export function RunDetail({ trace, onBack }: RunDetailProps) {
  const done = toDone(trace);
  const grounding = trace.steps.find((s) => s.type === "validation")?.verdict as
    | ValidationVerdict
    | undefined;

  return (
    <div className="space-y-4">
      <button
        type="button"
        onClick={onBack}
        className="rounded-md border border-border bg-surface-2 px-3 py-1.5 text-xs font-medium text-fg-muted shadow-sm transition-colors hover:text-fg"
      >
        ← Back to runs
      </button>

      <div>
        <div className="text-xs font-semibold uppercase tracking-wider text-fg-subtle">Goal</div>
        <div className="mt-1 whitespace-pre-wrap text-sm text-fg">{trace.goal}</div>
      </div>

      {trace.steps.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-fg-subtle">Trace</h2>
          <TraceView steps={trace.steps} />
        </section>
      )}

      {done.status === "pending_approval" ? (
        <div className="rounded-md border border-border bg-surface p-3 text-sm text-warn">
          This run is still awaiting plan approval — reopen it in the Chat tab to decide.
        </div>
      ) : (
        <section className="space-y-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-fg-subtle">Result</h2>
          <FinalCard done={done} grounding={grounding} />
        </section>
      )}
    </div>
  );
}
