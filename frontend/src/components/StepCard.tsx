import { useState } from "react";
import type {
  AgentStep,
  PlanStep,
  ValidationVerdict,
  VerdictPayload,
} from "../types/agent";
import type { CrisprEditReportOutput } from "../types/crispr";
import { isCrisprEditReport } from "../types/crispr";
import type { ScoreGuideOnTargetOutput } from "../types/on_target";
import { isScoreGuideOnTarget } from "../types/on_target";
import type { DesignPrimersOutput } from "../types/primers";
import { isDesignPrimersOutput } from "../types/primers";
import type { CompareStructuresOutput } from "../types/compare_structures";
import { isCompareStructuresOutput } from "../types/compare_structures";
import type { FindBestStructureOutput } from "../types/find_best_structure";
import { isFindBestStructureOutput } from "../types/find_best_structure";
import type { FetchInterproOutput } from "../types/interpro";
import { isInterproOutput } from "../types/interpro";
import type { FetchPdbOutput } from "../types/pdb_structure";
import { isPdbStructureOutput } from "../types/pdb_structure";
import type { FetchAlphaFoldOutput } from "../types/structure";
import { isAlphaFoldOutput } from "../types/structure";
import type { AlignMsaOutput } from "../types/msa";
import { isAlignMsaOutput } from "../types/msa";
import { cn } from "../lib/cn";
import { Card } from "./ui/Card";
import { Chip } from "./ui/Chip";
import { StatusDot } from "./ui/StatusDot";
import { CompareStructuresCard } from "./CompareStructuresCard";
import { CrisprReportCard } from "./CrisprReportCard";
import { OnTargetScoreCard } from "./OnTargetScoreCard";
import { FindBestStructureCard } from "./FindBestStructureCard";
import { InterproCard } from "./InterproCard";
import { PdbStructureCard } from "./PdbStructureCard";
import { MsaCard } from "./MsaCard";
import { PrimerPairsCard } from "./PrimerPairsCard";
import { StructureCard } from "./StructureCard";

interface StepCardProps {
  step: AgentStep;
  /** When true, the status dot pulses — used by TraceView for the live/last step. */
  live?: boolean;
}

// Color of the per-step status dot. The chip stays neutral; the dot carries meaning.
const DOT_COLOR: Record<string, string> = {
  plan: "text-accent",
  replan: "text-warn",
  approval_requested: "text-warn",
  approval_decision: "text-warn",
  llm_call: "text-fg-subtle",
  tool_call: "text-success",
  tool_error: "text-danger",
  refusal: "text-danger",
  critique: "text-accent",
  validation: "text-accent",
  final: "text-fg-muted",
};

// Low-signal steps start collapsed; everything substantive starts expanded.
const DEFAULT_COLLAPSED: Record<string, boolean> = {
  llm_call: true,
};

export function StepCard({ step, live }: StepCardProps) {
  const [open, setOpen] = useState(!DEFAULT_COLLAPSED[step.type]);
  const dot = DOT_COLOR[step.type] ?? "text-fg-muted";

  return (
    <Card className="animate-fade-in overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-surface-2"
      >
        <StatusDot className={dot} pulse={live} />
        <Chip>
          {step.idx}. {step.type}
        </Chip>
        <span className="min-w-0 flex-1 truncate text-xs text-fg-muted">
          {stepSummary(step)}
        </span>
        <span className="shrink-0 font-mono text-[10px] text-fg-subtle">
          {step.duration_ms}ms
        </span>
        <Chevron
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-fg-subtle transition-transform",
            open && "rotate-90",
          )}
        />
      </button>
      {open && (
        <div className="border-t border-border px-3 py-2.5 text-sm text-fg-muted">
          {renderStepBody(step)}
        </div>
      )}
    </Card>
  );
}

/**
 * The one-line, human-readable summary shown on the collapsed step header.
 * Kept intentionally free of the exact phrasing the detail bodies use, so the
 * trace reads cleanly and nothing is duplicated when a step is expanded.
 */
function stepSummary(step: AgentStep): string {
  switch (step.type) {
    case "plan":
    case "replan": {
      const n = step.plan?.steps.length ?? 0;
      const noun = `${n} step${n === 1 ? "" : "s"}`;
      return step.type === "replan" ? `Revised plan · ${noun}` : `Planned ${noun}`;
    }
    case "tool_call":
      return `Called ${step.tool_name ?? "tool"}`;
    case "tool_error":
      return `${step.tool_name ?? "Tool"} failed`;
    case "llm_call":
      return `Model call · ${step.stop_reason ?? "?"}`;
    case "critique": {
      const v = step.verdict as VerdictPayload | undefined;
      if (!v) return "Critique";
      return v.satisfies_goal ? "Critique · passes" : "Critique · needs revision";
    }
    case "validation": {
      const v = step.verdict as ValidationVerdict | undefined;
      if (!v) return "Grounding check";
      return v.ok ? "Grounding check · clear" : "Grounding check · flagged";
    }
    case "approval_requested":
      return "Awaiting your approval";
    case "approval_decision":
      return step.approved ? "Plan approved" : "Plan declined";
    case "refusal":
      return "Agent refused";
    case "final":
      return "Run complete";
    default:
      return step.type;
  }
}

function renderStepBody(step: AgentStep): React.ReactNode {
  switch (step.type) {
    case "plan":
    case "replan":
      return step.plan ? (
        <PlanBody steps={step.plan.steps} summary={step.plan.summary} />
      ) : null;
    case "tool_call":
      return <ToolCallBody step={step} />;
    case "tool_error":
      return (
        <div className="font-mono text-xs text-danger">
          <div className="font-semibold">{step.tool_name ?? "?"}</div>
          <div className="mt-1">{step.error}</div>
        </div>
      );
    case "llm_call":
      return (
        <div className="font-mono text-xs text-fg-subtle">
          stop={step.stop_reason ?? "?"} · in={step.input_tokens ?? 0} · out=
          {step.output_tokens ?? 0}
        </div>
      );
    case "critique": {
      const v = step.verdict as VerdictPayload | undefined;
      return v ? (
        <div className="space-y-1">
          <div className={cn("font-medium", v.satisfies_goal ? "text-success" : "text-warn")}>
            {v.satisfies_goal ? "✓ Satisfies goal" : "✗ Does not satisfy goal"}
          </div>
          <div className="text-xs text-fg-muted">{v.reason}</div>
          {v.concrete_complaints.length > 0 && (
            <ul className="ml-4 list-disc text-xs text-fg-muted">
              {v.concrete_complaints.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          )}
        </div>
      ) : null;
    }
    case "validation": {
      const v = step.verdict as ValidationVerdict | undefined;
      return v ? <ValidationBody v={v} /> : null;
    }
    case "approval_requested":
      return (
        <div className="text-xs text-fg-muted">
          Awaiting user approval — see the approval card below.
        </div>
      );
    case "approval_decision":
      return (
        <div className="text-xs text-fg-muted">
          User {step.approved ? "approved" : "declined"} the plan.
        </div>
      );
    case "refusal":
      return (
        <div className="text-xs italic text-fg-muted">
          Agent refused — see final response.
        </div>
      );
    case "final":
      return <div className="text-xs italic text-fg-subtle">Run complete.</div>;
    default:
      return null;
  }
}

function PlanBody({ steps, summary }: { steps: PlanStep[]; summary: string }) {
  return (
    <div>
      <div className="text-xs italic text-fg-subtle">{summary}</div>
      {steps.length > 0 && (
        <ol className="mt-2 list-decimal space-y-1 pl-5 text-xs text-fg-muted">
          {steps.map((s) => (
            <li key={s.idx}>
              <span className="font-medium text-fg">{s.description}</span>
              {s.expected_tool && (
                <span className="ml-2 font-mono text-accent">[{s.expected_tool}]</span>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function ValidationBody({ v }: { v: ValidationVerdict }) {
  const oodFlags = v.ood?.flags ?? [];
  const notes = v.model_uncertainty ?? [];
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={cn(
            "inline-flex items-center rounded border border-border bg-surface-2 px-1.5 py-0.5 text-[10px] font-medium",
            v.ok ? "text-success" : "text-warn",
          )}
        >
          {v.ok ? "✓ grounded" : "⚠ unverifiable claim(s) flagged"}
        </span>
        <span className="font-mono text-[10px] text-fg-subtle">
          mode: {v.mode}
          {v.enforced ? " · redacted" : ""}
        </span>
      </div>
      {v.summary && <div className="text-xs text-fg-muted">{v.summary}</div>}
      {oodFlags.length > 0 && (
        <div className="rounded border border-border bg-surface-2 p-1.5 text-xs text-warn">
          <div className="font-medium">
            Out-of-distribution input(s) — affected scores are extrapolations:
          </div>
          <ul className="ml-4 list-disc">
            {oodFlags.map((f, i) => (
              <li key={i}>
                <span className="font-mono">
                  {f.tool}.{f.field}
                </span>
                : {f.detail} (envelope: {f.envelope})
              </li>
            ))}
          </ul>
        </div>
      )}
      {notes.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-fg-subtle hover:text-fg-muted">
            model uncertainty ({notes.length})
          </summary>
          <ul className="ml-4 mt-1 space-y-0.5 text-fg-muted">
            {notes.map((n, i) => (
              <li key={i}>
                <span className="font-mono">
                  {n.tool}.{n.score_key}
                </span>
                : {n.note}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function ToolCallBody({ step }: { step: AgentStep }) {
  // Tool-specific custom renderers. When a tool produces a rich structured output,
  // we render it as a readable card instead of a JSON details blob. The default
  // collapsed-JSON fallback below still handles every other tool.
  const isCrispr =
    step.tool_name === "crispr_edit_report" &&
    step.tool_output &&
    isCrisprEditReport(step.tool_output);
  const isOnTarget =
    step.tool_name === "score_guide_on_target" &&
    step.tool_output &&
    isScoreGuideOnTarget(step.tool_output);
  const isPrimers =
    step.tool_name === "design_primers" &&
    step.tool_output &&
    isDesignPrimersOutput(step.tool_output);
  const isStructure =
    step.tool_name === "fetch_alphafold_structure" &&
    step.tool_output &&
    isAlphaFoldOutput(step.tool_output);
  const isPdbStructure =
    step.tool_name === "fetch_pdb_structure" &&
    step.tool_output &&
    isPdbStructureOutput(step.tool_output);
  const isFindBest =
    step.tool_name === "find_best_structure" &&
    step.tool_output &&
    isFindBestStructureOutput(step.tool_output);
  const isInterpro =
    step.tool_name === "fetch_interpro_domains" &&
    step.tool_output &&
    isInterproOutput(step.tool_output);
  const isCompare =
    step.tool_name === "compare_structures" &&
    step.tool_output &&
    isCompareStructuresOutput(step.tool_output);
  const isMsa =
    step.tool_name === "align_msa" &&
    step.tool_output &&
    isAlignMsaOutput(step.tool_output);

  return (
    <div className="space-y-2">
      <div className="font-mono text-xs font-semibold text-success">
        {step.tool_name}
      </div>
      {step.tool_input && (
        <details className="text-xs">
          <summary className="cursor-pointer text-fg-subtle hover:text-fg-muted">
            input
          </summary>
          <pre className="mt-1 max-h-40 overflow-auto rounded border border-border bg-bg p-2 font-mono text-[11px] text-fg-muted">
            {JSON.stringify(step.tool_input, null, 2)}
          </pre>
        </details>
      )}
      {isCrispr && step.tool_output && (
        <CrisprReportCard report={step.tool_output as unknown as CrisprEditReportOutput} />
      )}
      {isOnTarget && step.tool_output && (
        <OnTargetScoreCard output={step.tool_output as unknown as ScoreGuideOnTargetOutput} />
      )}
      {isPrimers && step.tool_output && (
        <PrimerPairsCard output={step.tool_output as unknown as DesignPrimersOutput} />
      )}
      {isStructure && step.tool_output && (
        <StructureCard structure={step.tool_output as unknown as FetchAlphaFoldOutput} />
      )}
      {isPdbStructure && step.tool_output && (
        <PdbStructureCard structure={step.tool_output as unknown as FetchPdbOutput} />
      )}
      {isFindBest && step.tool_output && (
        <FindBestStructureCard result={step.tool_output as unknown as FindBestStructureOutput} />
      )}
      {isInterpro && step.tool_output && (
        <InterproCard output={step.tool_output as unknown as FetchInterproOutput} />
      )}
      {isCompare && step.tool_output && (
        <CompareStructuresCard result={step.tool_output as unknown as CompareStructuresOutput} />
      )}
      {isMsa && step.tool_output && (
        <MsaCard output={step.tool_output as unknown as AlignMsaOutput} />
      )}
      {step.tool_output &&
        !isCrispr &&
        !isOnTarget &&
        !isPrimers &&
        !isStructure &&
        !isPdbStructure &&
        !isFindBest &&
        !isInterpro &&
        !isCompare &&
        !isMsa && (
          <details className="text-xs">
            <summary className="cursor-pointer text-fg-subtle hover:text-fg-muted">
              output
            </summary>
            <pre className="mt-1 max-h-60 overflow-auto rounded border border-border bg-bg p-2 font-mono text-[11px] text-fg-muted">
              {JSON.stringify(step.tool_output, null, 2)}
            </pre>
          </details>
        )}
    </div>
  );
}

function Chevron({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <polyline points="9 6 15 12 9 18" />
    </svg>
  );
}
