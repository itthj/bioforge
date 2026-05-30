import type { AgentStep, PlanStep, ValidationVerdict, VerdictPayload } from "../types/agent";
import type { CrisprEditReportOutput } from "../types/crispr";
import { isCrisprEditReport } from "../types/crispr";
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
import { CompareStructuresCard } from "./CompareStructuresCard";
import { CrisprReportCard } from "./CrisprReportCard";
import { FindBestStructureCard } from "./FindBestStructureCard";
import { InterproCard } from "./InterproCard";
import { PdbStructureCard } from "./PdbStructureCard";
import { PrimerPairsCard } from "./PrimerPairsCard";
import { StructureCard } from "./StructureCard";

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
  validation: { badge: "bg-teal-100 text-teal-800", border: "border-teal-200" },
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
    case "critique": {
      const v = step.verdict as VerdictPayload | undefined;
      return v ? (
        <div className="space-y-1">
          <div className="font-medium">
            {v.satisfies_goal ? "✓ Satisfies goal" : "✗ Does not satisfy goal"}
          </div>
          <div className="text-xs text-slate-600">{v.reason}</div>
          {v.concrete_complaints.length > 0 && (
            <ul className="ml-4 list-disc text-xs text-slate-600">
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

function ValidationBody({ v }: { v: ValidationVerdict }) {
  const oodFlags = v.ood?.flags ?? [];
  const notes = v.model_uncertainty ?? [];
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium ${
            v.ok ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"
          }`}
        >
          {v.ok ? "✓ grounded" : "⚠ unverifiable claim(s) flagged"}
        </span>
        <span className="font-mono text-[10px] text-slate-400">
          mode: {v.mode}
          {v.enforced ? " · redacted" : ""}
        </span>
      </div>
      {v.summary && <div className="text-xs text-slate-600">{v.summary}</div>}
      {oodFlags.length > 0 && (
        <div className="rounded border border-amber-200 bg-amber-50 p-1.5 text-xs text-amber-800">
          <div className="font-medium">Out-of-distribution input(s) — affected scores are extrapolations:</div>
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
          <summary className="cursor-pointer text-slate-500 hover:text-slate-700">
            model uncertainty ({notes.length})
          </summary>
          <ul className="ml-4 mt-1 space-y-0.5 text-slate-600">
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
      {isCrispr && step.tool_output && (
        <CrisprReportCard report={step.tool_output as unknown as CrisprEditReportOutput} />
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
      {step.tool_output &&
        !isCrispr &&
        !isPrimers &&
        !isStructure &&
        !isPdbStructure &&
        !isFindBest &&
        !isInterpro &&
        !isCompare && (
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
