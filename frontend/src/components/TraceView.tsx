import type { AgentStep } from "../types/agent";
import { StepCard } from "./StepCard";

interface TraceViewProps {
  steps: AgentStep[];
  /** When true, the last step shows a live pulse (the agent is still working). */
  live?: boolean;
}

export function TraceView({ steps, live }: TraceViewProps) {
  if (steps.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-surface p-6 text-center text-sm text-fg-subtle">
        Steps will stream here as the agent runs.
      </div>
    );
  }
  const lastIdx = steps.length - 1;
  return (
    <div className="space-y-2">
      {steps.map((step, i) => (
        <StepCard
          key={`${step.idx}-${step.type}`}
          step={step}
          live={live && i === lastIdx}
        />
      ))}
    </div>
  );
}
