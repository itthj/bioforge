import type { AgentStep, PlanStep } from "../types/agent";

interface StepCardProps {
  step: AgentStep;
}

const TYPE_STYLES: Record<string, { badge: string; border: string }> = {
  plan: { badge: "bg-indigo-100 text-indigo-800", border: "border-indigo-200" },
  replan: { badge: "bg-amber-100 text-amber-800", border: "border-amber-200" },
  approval_requested: {
    badge: "bg-orange-100 text-orange-800",
    border: "border-orange-200",
  },
  approval_decision: {
    badge: "bg-orange-100 text-orange-800",
    border: "border-orange-200",
  },
  llm_call: { badge: "bg-slate-100 text-slate-700", border: "border-slate-200" },
  tool_call: { badge: "bg-emerald-100 text-emerald-800", border: "border-emerald-200" },
  tool_error: { badge: "bg-rose-100 text-rose-800", border: "border-rose-200" },
  refusal: { badge: "bg-rose-100 text-rose-800", border: "border-rose-200" },
  critique: { badge: "bg-purple-100 text-purple-800", border: "border-purple-200" },
  final: { badge: "bg-slate-200 text-slate-800", border: "border-slate-300" },
};

export function StepCard({ step }: StepCardProps) {
  const style = TYPE_STYLES[step.type] ?? TYPE_STYLES.llm_call;

  return (
    <div className={`rounded-md border bg-white p-3 shadow-sm ${style.border}`}>
      <div className="flex items-center justify-between text-xs">
        <span
          className={`inline-flex items-center gap-1 rounded px-2 py-0.5 font-mono font-medium ${style.badge}`}
        >
          {step.idx}. {step.type}
        </span>
        <span className="text-slate-400">{step.duration_ms}ms</span>
      </div>
      <div className="mt-2 text-sm text-slate-700">{renderStepBody(step)}</div>
    </div>
  );
}

function renderStepBody(step: AgentStep): React.ReactNode {
  switch (step.type) {
    case "plan":
    case "replan":
      return step.plan ? <PlanBody steps={step.plan.steps} summary={step.plan.summary} /> : null;
    case "tool_call":
      return <ToolCallBody step={step} />;
    case "tool_error":
      return (
        <div className="font-mono text-xs text-rose-700">
          <div className="font-semibold">{step.tool_name ?? "?"}</div>
          <div className="mt-1">{step.error}</div>
        </div>
      );
    case "llm_call":
      return (
        <div className="font-mono text-xs text-slate-500">
          stop={step.stop_reason ?? "?"} · in={step.input_tokens ?? 0} · out=
          {step.output_tokens ?? 0}
        </div>
      );
    case "critique":
      return step.verdict ? (
        <div className="space-y-1">
          <div className="font-medium">
            {step.verdict.satisfies_goal ? "✓ Satisfies goal" : "✗ Does not satisfy goal"}
          </div>
          <div className="text-xs text-slate-600">{step.verdict.reason}</div>
          {step.verdict.concrete_complaints.length > 0 && (
            <ul className="ml-4 list-disc text-xs text-slate-600">
              {step.verdict.concrete_complaints.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          )}
        </div>
      ) : null;
    case "approval_requested":
      return (
        <div className="text-xs text-slate-600">
          Awaiting user approval — see the approval card below.
        </div>
      );
    case "approval_decision":
      return (
        <div className="text-xs text-slate-600">
          User {step.approved ? "approved" : "declined"} the plan.
        </div>
      );
    case "refusal":
      return (
        <div className="text-xs italic text-slate-600">
          Agent refused — see final response.
        </div>
      );
    case "final":
      return (
        <div className="text-xs italic text-slate-500">Run complete.</div>
      );
    default:
      return null;
  }
}

function PlanBody({ steps, summary }: { steps: PlanStep[]; summary: string }) {
  return (
    <div>
      <div className="text-xs italic text-slate-600">{summary}</div>
      {steps.length > 0 && (
        <ol className="mt-2 list-decimal space-y-1 pl-5 text-xs">
          {steps.map((s) => (
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
      )}
    </div>
  );
}

function ToolCallBody({ step }: { step: AgentStep }) {
  return (
    <div className="space-y-2">
      <div className="font-mono text-xs font-semibold text-emerald-800">
        {step.tool_name}
      </div>
      {step.tool_input && (
        <details className="text-xs">
          <summary className="cursor-pointer text-slate-500 hover:text-slate-700">
            input
          </summary>
          <pre className="mt-1 max-h-40 overflow-auto rounded bg-slate-50 p-2 font-mono text-[11px] text-slate-700">
            {JSON.stringify(step.tool_input, null, 2)}
          </pre>
        </details>
      )}
      {step.tool_output && (
        <details className="text-xs">
          <summary className="cursor-pointer text-slate-500 hover:text-slate-700">
            output
          </summary>
          <pre className="mt-1 max-h-60 overflow-auto rounded bg-slate-50 p-2 font-mono text-[11px] text-slate-700">
            {JSON.stringify(step.tool_output, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}
