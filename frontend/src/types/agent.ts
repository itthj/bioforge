// Types mirror the backend's AgentStep / AgentResult shape. Kept hand-written for now;
// when the backend's OpenAPI schema is exported, we can codegen these.

// User-set autonomy level sent with a run. "auto" pauses only for expensive/destructive
// plans; "review" pauses after planning on any non-trivial plan so the user approves the
// plan before any tool runs.
export type Autonomy = "auto" | "review";

export type StepType =
  | "plan"
  | "replan"
  | "approval_requested"
  | "approval_decision"
  | "llm_call"
  | "tool_call"
  | "tool_error"
  | "refusal"
  | "critique"
  | "validation"
  | "final";

export interface AgentStep {
  idx: number;
  type: StepType;
  duration_ms: number;
  stop_reason?: string | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  cache_creation_tokens?: number | null;
  cache_read_tokens?: number | null;
  tool_name?: string | null;
  tool_input?: Record<string, unknown> | null;
  tool_output?: Record<string, unknown> | null;
  error?: string | null;
  plan?: PlanPayload | null;
  verdict?: VerdictPayload | ValidationVerdict | null;
  approval_reasons?: string[] | null;
  approved?: boolean | null;
}

export interface PlanStep {
  idx: number;
  description: string;
  expected_tool: string | null;
  rationale: string;
}

export interface PlanPayload {
  is_trivial: boolean;
  summary: string;
  steps: PlanStep[];
}

export interface VerdictPayload {
  satisfies_goal: boolean;
  reason: string;
  concrete_complaints: string[];
}

export interface OodFlag {
  tool: string;
  field: string;
  detail: string;
  envelope: string;
  message: string;
}

export interface ModelUncertaintyNote {
  tool: string;
  score_key: string;
  note: string;
}

// A single numeric token in the response, with its grounding outcome + provenance.
// `start`/`end` are character offsets into the (draft) response text.
export interface NumericClaimVerdict {
  text: string;
  value: number;
  is_percent: boolean;
  start: number;
  end: number;
  status: "grounded" | "unsupported";
  matched_path: string | null;
  matched_value: number | null;
}

// A structured biological identifier (rsID, RefSeq, Ensembl, ClinVar, PDB) in the response.
export interface EntityClaimVerdict {
  text: string;
  kind: string;
  start: number;
  end: number;
  status: "grounded" | "unsupported";
  matched_path: string | null;
}

// The validation/grounding step (BioForge v4 §4/§6) reuses the `verdict` slot with this shape:
// grounding status, per-claim verdicts (with offsets + provenance), the OOD report, and the
// per-model uncertainty posture.
export interface ValidationVerdict {
  ok: boolean;
  summary: string;
  mode: string;
  enforced: boolean;
  ood: { ok: boolean; checked: number; flags: OodFlag[] };
  model_uncertainty: ModelUncertaintyNote[];
  // Optional: present on traces produced once per-claim grounding shipped. Drives the
  // inline hover-to-verify highlighting in FinalCard.
  numeric_claims?: NumericClaimVerdict[];
  entity_claims?: EntityClaimVerdict[];
}

export interface UsageSummary {
  input_tokens: number;
  output_tokens: number;
  cache_creation_tokens: number;
  cache_read_tokens: number;
  cost_usd: number;
  model: string;
}

export type AgentStatus =
  | "completed"
  | "completed_after_replan"
  | "critique_failed"
  | "refused"
  | "error"
  | "iteration_cap"
  | "pending_approval"
  | "cancelled";

export interface AgentDoneEvent {
  trace_id: string;
  status: AgentStatus;
  response_text: string;
  model: string;
  usage: UsageSummary | null;
  pending_plan: PlanPayload | null;
  approval_reasons: string[];
}

// Emitted first when a run is dispatched to the Celery worker (celery mode): the run isn't
// in-process, so the client learns its trace_id here to enable Stop -> /cancel. Absent in
// inline mode.
export interface AgentQueuedEvent {
  trace_id: string;
  status: string;
  job_backend: string;
}

export type SseEvent =
  | { event: "step"; data: AgentStep }
  | { event: "done"; data: AgentDoneEvent }
  | { event: "queued"; data: AgentQueuedEvent }
  | { event: "error"; data: { message: string } };
