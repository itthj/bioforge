import type { AgentDoneEvent, ValidationVerdict } from "../types/agent";
import { Card } from "./ui/Card";
import { GroundedResponse } from "./GroundedResponse";

interface FinalCardProps {
  done: AgentDoneEvent;
  // The grounding verdict from the run's `validation` step, if it ran. Drives the inline
  // hover-to-verify highlighting and the header trust chip.
  grounding?: ValidationVerdict | null;
}

// `text` is a token color class; the badge chrome is shared below.
const STATUS_STYLES: Record<string, { label: string; text: string }> = {
  completed: { label: "Completed", text: "text-success" },
  completed_after_replan: {
    label: "Completed (after replan)",
    text: "text-success",
  },
  critique_failed: {
    label: "Critique failed — review carefully",
    text: "text-warn",
  },
  refused: { label: "Refused", text: "text-danger" },
  error: { label: "Error", text: "text-danger" },
  iteration_cap: { label: "Iteration cap hit", text: "text-warn" },
  cancelled: { label: "Cancelled", text: "text-fg-muted" },
  pending_approval: { label: "Awaiting approval", text: "text-warn" },
};

export function FinalCard({ done, grounding }: FinalCardProps) {
  if (done.status === "pending_approval") return null;

  const style = STATUS_STYLES[done.status] ?? {
    label: done.status,
    text: "text-fg-muted",
  };

  // Inline grounding highlights only when the validator ran and did NOT redact (enforce
  // mode rewrites the text, invalidating the offsets).
  const numericClaims = grounding && !grounding.enforced ? grounding.numeric_claims ?? [] : [];
  const entityClaims = grounding && !grounding.enforced ? grounding.entity_claims ?? [] : [];
  const claimCount = numericClaims.length + entityClaims.length;
  const flaggedCount =
    numericClaims.filter((c) => c.status === "unsupported").length +
    entityClaims.filter((c) => c.status === "unsupported").length;

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span
            className={`inline-flex items-center rounded border border-border bg-surface-2 px-2 py-0.5 text-xs font-medium ${style.text}`}
          >
            {style.label}
          </span>
          {claimCount > 0 && (
            <span
              className={`inline-flex items-center rounded border border-border bg-surface-2 px-2 py-0.5 text-[11px] font-medium ${
                flaggedCount === 0 ? "text-success" : "text-warn"
              }`}
              title={
                flaggedCount === 0
                  ? "Every checked value traced to a tool result this run."
                  : `${flaggedCount} value(s) could not be traced to a tool result this run.`
              }
            >
              {flaggedCount === 0 ? "✓ grounded" : `⚠ ${flaggedCount} to check`}
            </span>
          )}
        </div>
        {done.usage && (
          <span className="font-mono text-xs text-fg-subtle">
            {done.usage.input_tokens + done.usage.output_tokens} tok · $
            {done.usage.cost_usd.toFixed(4)}
          </span>
        )}
      </div>

      <div className="mt-3">
        {claimCount > 0 ? (
          <GroundedResponse
            text={done.response_text}
            numericClaims={numericClaims}
            entityClaims={entityClaims}
          />
        ) : (
          <div className="whitespace-pre-wrap text-sm text-fg">{done.response_text}</div>
        )}
      </div>

      {claimCount > 0 && (
        <div className="mt-2 text-[11px] text-fg-subtle">
          Underlined values were checked against this run's tool results — hover any one to
          see its source.
        </div>
      )}

      <div className="mt-3 border-t border-border pt-3">
        <div className="font-mono text-[11px] text-fg-subtle">
          trace_id: {done.trace_id} · model: {done.model}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
          <span className="font-semibold uppercase tracking-wider text-fg-subtle">
            Provenance
          </span>
          <a
            href={`/traces/${done.trace_id}/script`}
            download
            className="text-accent hover:underline"
            title="Runnable Python script that re-executes this run's deterministic tool pipeline"
          >
            Reproduce (.py)
          </a>
          <span className="text-fg-subtle" aria-hidden>
            ·
          </span>
          <a
            href={`/traces/${done.trace_id}/report`}
            download
            className="text-accent hover:underline"
            title="Publication-grade Markdown methods & reproducibility record"
          >
            Methods report (.md)
          </a>
          <span className="text-fg-subtle" aria-hidden>
            ·
          </span>
          <a
            href={`/traces/${done.trace_id}/ro-crate`}
            download
            className="text-accent hover:underline"
            title="RO-Crate 1.1 research object (JSON-LD)"
          >
            RO-Crate (.json)
          </a>
          <span className="text-fg-subtle" aria-hidden>
            ·
          </span>
          <a
            href={`/traces/${done.trace_id}/manifest`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-accent hover:underline"
            title="Content-addressed run manifest (JSON)"
          >
            Manifest (JSON)
          </a>
        </div>
      </div>
    </Card>
  );
}
