/**
 * Standalone visual showcase of the dark-console redesign. Renders the REAL new
 * components with mock data — no backend, no network — so the improvements are visible
 * (and interactive: expand trace steps, hover grounded values, click identifiers).
 *
 * Served by Vite at /showcase.html. Not part of the app; safe to delete.
 */
import { StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { TraceView } from "./components/TraceView";
import { FinalCard } from "./components/FinalCard";
import { ApprovalCard } from "./components/ApprovalCard";
import { ChatInput } from "./components/ChatInput";
import type {
  AgentDoneEvent,
  AgentStep,
  EntityClaimVerdict,
  NumericClaimVerdict,
  ValidationVerdict,
} from "./types/agent";

// --- mock data ----------------------------------------------------------------------

const RESPONSE =
  "The reverse complement's GC content is 50%. The variant rs80357064 is catalogued in " +
  "ClinVar, and the haemoglobin structure 4HHB offers structural context for the locus.";

const at = (s: string) => RESPONSE.indexOf(s);

const NUMERIC_CLAIMS: NumericClaimVerdict[] = [
  {
    text: "50%",
    value: 50,
    is_percent: true,
    start: at("50%"),
    end: at("50%") + "50%".length,
    status: "grounded",
    matched_path: "gc_content.gc_percent",
    matched_value: 50,
  },
];

const ENTITY_CLAIMS: EntityClaimVerdict[] = [
  {
    text: "rs80357064",
    kind: "rsid",
    start: at("rs80357064"),
    end: at("rs80357064") + "rs80357064".length,
    status: "grounded",
    matched_path: "input[0]",
  },
  {
    text: "4HHB",
    kind: "pdb",
    start: at("4HHB"),
    end: at("4HHB") + "4HHB".length,
    status: "unsupported",
    matched_path: null,
  },
];

const VALIDATION: ValidationVerdict = {
  ok: false,
  summary: "2 of 3 claims traced to tool results this run; 1 could not be traced.",
  mode: "annotate",
  enforced: false,
  ood: { ok: true, checked: 1, flags: [] },
  model_uncertainty: [],
  numeric_claims: NUMERIC_CLAIMS,
  entity_claims: ENTITY_CLAIMS,
};

const STEPS: AgentStep[] = [
  {
    idx: 0,
    type: "plan",
    duration_ms: 412,
    plan: {
      is_trivial: false,
      summary: "Reverse-complement the sequence, compute GC content, then verify every claim.",
      steps: [
        { idx: 0, description: "Reverse-complement the input sequence", expected_tool: "reverse_complement", rationale: "The question is about the reverse complement." },
        { idx: 1, description: "Compute GC content of the result", expected_tool: "gc_content", rationale: "Answers the user's question." },
      ],
    },
  },
  { idx: 1, type: "llm_call", duration_ms: 1180, stop_reason: "tool_use", input_tokens: 1240, output_tokens: 88 },
  {
    idx: 2,
    type: "tool_call",
    duration_ms: 6,
    tool_name: "gc_content",
    tool_input: { sequence: "GCATGCATGC" },
    tool_output: { gc_percent: 50, total_length: 10, gc_count: 5, version: "1.0.0" },
  },
  { idx: 3, type: "validation", duration_ms: 12, verdict: VALIDATION },
  { idx: 4, type: "final", duration_ms: 0 },
] as AgentStep[];

const DONE: AgentDoneEvent = {
  trace_id: "trace_demo_001",
  status: "completed",
  response_text: RESPONSE,
  model: "claude-sonnet-4-6",
  usage: { input_tokens: 1240, output_tokens: 88, cache_creation_tokens: 0, cache_read_tokens: 0, cost_usd: 0.0042, model: "claude-sonnet-4-6" },
  pending_plan: null,
  approval_reasons: [],
};

const PENDING: AgentDoneEvent = {
  trace_id: "trace_demo_002",
  status: "pending_approval",
  response_text: "",
  model: "claude-sonnet-4-6",
  usage: null,
  pending_plan: {
    is_trivial: false,
    summary: "BLAST the sequence against nt, then summarise the top homologs.",
    steps: [
      { idx: 0, description: "BLAST the query against NCBI nt", expected_tool: "blast", rationale: "Find homologous sequences." },
      { idx: 1, description: "Summarise the top hits", expected_tool: null, rationale: "Answer the user." },
    ],
  },
  approval_reasons: [
    "Review mode: you chose to approve the plan before any tools run.",
    "Step 0 (blast): expensive — long runtime and/or external API cost.",
  ],
};

// --- layout -------------------------------------------------------------------------

function Section({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <div>
        <h2 className="text-sm font-semibold text-fg">{title}</h2>
        <p className="text-xs text-fg-subtle">{hint}</p>
      </div>
      {children}
    </section>
  );
}

function AutonomyToggleDemo() {
  return (
    <div className="inline-flex rounded-md border border-border bg-surface p-0.5">
      <span className="rounded bg-surface-2 px-2.5 py-1 text-xs font-medium text-fg">Auto</span>
      <span className="rounded px-2.5 py-1 text-xs font-medium text-fg-subtle">Review plan</span>
    </div>
  );
}

function Showcase() {
  return (
    <div className="mx-auto max-w-3xl space-y-10 px-4 py-10">
      <header className="flex items-center gap-2.5">
        <span className="inline-block h-2.5 w-2.5 rounded-full bg-accent" aria-hidden />
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-fg">BioForge</h1>
          <p className="text-xs text-fg-subtle">
            UI improvements showcase — dark console, reasoning trace, interactive grounding (mock data, no backend).
          </p>
        </div>
      </header>

      <Section title="Steerable autonomy" hint="Set how much the agent runs on its own before pausing for you.">
        <AutonomyToggleDemo />
      </Section>

      <Section title="Goal input" hint="Example-goal chips + a mono textarea for sequences.">
        <ChatInput onSubmit={() => {}} />
      </Section>

      <Section title="Agent reasoning trace" hint="A connected timeline — click any step to expand its detail.">
        <TraceView steps={STEPS} />
      </Section>

      <Section title="Final answer with interactive grounding" hint="Hover an underlined value to see what verified it; click an identifier to open its database.">
        <FinalCard done={DONE} grounding={VALIDATION} />
      </Section>

      <Section title="Plan approval (Review mode)" hint="Nothing runs until you approve the plan.">
        <ApprovalCard done={PENDING} onDecision={() => {}} />
      </Section>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Showcase />
  </StrictMode>,
);
