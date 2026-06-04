/**
 * Standalone visual showcase of the dark-console redesign. Renders the REAL new
 * components with mock data — no backend, no network — so the improvements are visible
 * (and interactive: expand trace steps, hover grounded values, click identifiers, edit
 * the plan, click guide rows to link the genome browser, export CSV/SVG).
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
import { CrisprReportCard } from "./components/CrisprReportCard";
import { OnTargetScoreCard } from "./components/OnTargetScoreCard";
import { PrimerPairsCard } from "./components/PrimerPairsCard";
import { ReliabilityDiagram } from "./components/ReliabilityDiagram";
import type {
  AgentDoneEvent,
  AgentStep,
  EntityClaimVerdict,
  NumericClaimVerdict,
  ValidationVerdict,
} from "./types/agent";
import type { CrisprEditReportOutput, GuideReport } from "./types/crispr";
import type { ScoreGuideOnTargetOutput } from "./types/on_target";
import type { DesignPrimersOutput } from "./types/primers";
import type { ReliabilityCurve } from "./types/benchmarks";

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

// --- CRISPR edit report (P2a: linked selection + guide CSV export + genome browser) --
// Coordinates are derived from named parts so the IGV view + the card stay consistent.
const C_PRE = "GGGGCCACTAGGGAC"; // 15
const C_P1 = "GAGTCCGAGCAGAAGAAGAA"; // 20 — protospacer, guide 1
const C_PAM1 = "GGG"; // 3
const C_MID = "AGGGTGGGCAGGTGGCAATT"; // 20
const C_P2 = "TGCCAGGCAGAAAGCAAACG"; // 20 — protospacer, guide 2
const C_PAM2 = "AGG"; // 3
const C_SUF = "TTGGGCTCCC"; // 10
const CRISPR_TARGET = C_PRE + C_P1 + C_PAM1 + C_MID + C_P2 + C_PAM2 + C_SUF;
const G1_START = C_PRE.length; // 15
const G1_PAM = G1_START + C_P1.length; // 35
const G2_START = G1_PAM + C_PAM1.length + C_MID.length; // 58
const G2_PAM = G2_START + C_P2.length; // 78

const GUIDE_1: GuideReport = {
  rank: 1,
  protospacer: C_P1,
  pam_sequence: C_PAM1,
  strand: "+",
  protospacer_start: G1_START,
  protospacer_end: G1_PAM,
  pam_start: G1_PAM,
  pam_end: G1_PAM + 3,
  heuristic_score: 0.71,
  on_target_score: 0.62,
  recommendation_score: 0.66,
  recommendation_label: "preferred",
  rationale: ["High on-target score", "No poly-T run", "Clean PAM"],
  off_target_summary: {
    searched: true,
    database: "GRCh38 (BLAST)",
    high_risk_count: 0,
    medium_risk_count: 1,
    low_risk_count: 6,
    top_hits: [],
    caveats: [],
  },
  edit_outcome_summary: {
    cut_position_fwd: G1_PAM - 3,
    frameshift_probability: 0.78,
    no_edit_probability: 0.11,
    top_outcomes: [],
  },
};

const GUIDE_2: GuideReport = {
  rank: 2,
  protospacer: C_P2,
  pam_sequence: C_PAM2,
  strand: "+",
  protospacer_start: G2_START,
  protospacer_end: G2_PAM,
  pam_start: G2_PAM,
  pam_end: G2_PAM + 3,
  heuristic_score: 0.55,
  on_target_score: 0.48,
  recommendation_score: 0.51,
  recommendation_label: "acceptable",
  rationale: ["Moderate on-target score", "PAM verified"],
  off_target_summary: {
    searched: true,
    database: "GRCh38 (BLAST)",
    high_risk_count: 0,
    medium_risk_count: 2,
    low_risk_count: 9,
    top_hits: [],
    caveats: [],
  },
  edit_outcome_summary: {
    cut_position_fwd: G2_PAM - 3,
    frameshift_probability: 0.66,
    no_edit_probability: 0.18,
    top_outcomes: [],
  },
};

const CRISPR_REPORT: CrisprEditReportOutput = {
  target_length: CRISPR_TARGET.length,
  target_sequence: CRISPR_TARGET,
  pam: "NGG",
  num_guides_considered: 8,
  recommended_guide: GUIDE_1,
  guides: [GUIDE_1, GUIDE_2],
  tool_chain: ["design_guides", "score_guide_on_target", "edit_outcome", "find_offtargets"],
  caveats: [
    "On-target scoring is a transparent rule-based proxy plus opt-in deep models — not a single ground truth.",
  ],
};

// --- On-target score (P2a: multi-scorer + CSV export) --------------------------------
const ON_TARGET: ScoreGuideOnTargetOutput = {
  protospacer: C_P1,
  pam: C_PAM1,
  on_target_score: 0.621,
  score_breakdown: {
    gc_component: 0.55,
    polyt_component: 1.0,
    position_component: 0.6,
    dinucleotide_component: 0.5,
    component_weights: { gc: 0.3, polyt: 0.2, position: 0.35, dinucleotide: 0.15 },
  },
  deepcrispr_on_target_score: 0.71,
  deepcrispr_model_version: "ontar_cnn_reg_seq@master",
  azimuth_rs2_on_target_score: 0.58,
  azimuth_rs2_model_version: "V3_model_nopos@dbd30b9",
  caveats: ["on_target_score is a transparent rule-based proxy of published design rules."],
};

// --- PCR primers (P2a: CSV export) ---------------------------------------------------
const PRIMERS: DesignPrimersOutput = {
  template_length: 240,
  target_start: 80,
  target_end: 160,
  num_returned: 2,
  primer3_warnings: [],
  caveats: ["primer3 does not verify specificity against a genome."],
  primer_pairs: [
    {
      rank: 0,
      forward_sequence: "GCAATTCCCAATGGCAAAGGT",
      forward_tm: 60.0,
      forward_gc_percent: 47.6,
      forward_start: 12,
      forward_length: 21,
      reverse_sequence: "ATTAAGCCACGTTCACCGGT",
      reverse_tm: 59.9,
      reverse_gc_percent: 50.0,
      reverse_start: 191,
      reverse_length: 20,
      product_size: 180,
      pair_penalty: 0.842,
    },
    {
      rank: 1,
      forward_sequence: "TGCCCAATGGCAAAGGTGAA",
      forward_tm: 60.4,
      forward_gc_percent: 50.0,
      forward_start: 18,
      forward_length: 20,
      reverse_sequence: "GCCACGTTCACCGGTTAAGA",
      reverse_tm: 60.1,
      reverse_gc_percent: 55.0,
      reverse_start: 188,
      reverse_length: 20,
      product_size: 171,
      pair_penalty: 1.337,
    },
  ],
};

// --- Reliability curve (P2a: CSV + SVG export of a real benchmark figure) -------------
const RELIABILITY: ReliabilityCurve = {
  n: 1234,
  n_bins: 5,
  bins: [
    { bin_index: 0, n: 247, predicted_mean: 0.12, observed_mean: 0.83, observed_sem: 0.04, predicted_low: 0.0, predicted_high: 0.2 },
    { bin_index: 1, n: 247, predicted_mean: 0.27, observed_mean: 0.95, observed_sem: 0.05, predicted_low: 0.2, predicted_high: 0.33 },
    { bin_index: 2, n: 246, predicted_mean: 0.41, observed_mean: 1.04, observed_sem: 0.05, predicted_low: 0.33, predicted_high: 0.48 },
    { bin_index: 3, n: 247, predicted_mean: 0.55, observed_mean: 1.18, observed_sem: 0.06, predicted_low: 0.48, predicted_high: 0.62 },
    { bin_index: 4, n: 247, predicted_mean: 0.73, observed_mean: 1.36, observed_sem: 0.07, predicted_low: 0.62, predicted_high: 1.0 },
  ],
  monotonicity_rho: 0.83,
  kind: "regression_ranking",
  predicted_label: "DeepCRISPR on-target score",
  observed_label: "Chari-2015 measured efficiency",
  caveat: "Ranking-reliability curve: the score is not a probability calibration; y=x is not the target.",
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
            UI showcase — dark console, reasoning trace, interactive grounding, linked genome
            browser, figure/data export, and an editable plan (mock data, no backend).
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

      <Section
        title="CRISPR edit report — linked genome browser + CSV export (new)"
        hint="Click a guide row to select it (and center it once you Load the genome browser); Export CSV pulls the full guide table out for a paper."
      >
        <CrisprReportCard report={CRISPR_REPORT} />
      </Section>

      <Section
        title="On-target scores — multi-model + CSV export (new)"
        hint="Rule-based + opt-in deep scorers side by side (disagreement is signal); CSV exports all of them."
      >
        <OnTargetScoreCard output={ON_TARGET} />
      </Section>

      <Section
        title="PCR primers — CSV export (new)"
        hint="Export CSV gives an ordering-sheet-ready table (both strands, Tm, GC, product size)."
      >
        <PrimerPairsCard output={PRIMERS} />
      </Section>

      <Section
        title="Reliability curve — CSV + SVG export (new)"
        hint="A real benchmark figure; export the per-bin data as CSV or the figure itself as a standalone SVG for a paper."
      >
        <ReliabilityDiagram curve={RELIABILITY} />
      </Section>

      <Section
        title="Plan approval — now editable before you approve (new)"
        hint="Reword, reorder (↑/↓), or delete steps, then Approve. Editing STEERS the agent — it is guidance, not a hard constraint on which tools run."
      >
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
