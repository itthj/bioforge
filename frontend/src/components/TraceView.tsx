import type { AgentStep } from "../types/agent";
import { StepCard } from "./StepCard";

interface TraceViewProps {
  steps: AgentStep[];
}

export function TraceView({ steps }: TraceViewProps) {
  if (steps.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-slate-300 bg-white p-6 text-center text-sm text-slate-400">
        Steps will stream here as the agent runs.
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {steps.map((step) => (
        <StepCard key={`${step.idx}-${step.type}`} step={step} />
      ))}
    </div>
  );
}
