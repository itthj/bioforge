import type { AgentStep } from "./agent";

// Row shape from GET /projects/{id}/traces — the run-history feed.
export interface TraceSummary {
  trace_id: string;
  project_id: string;
  goal: string;
  status: string;
  model: string;
  cost_usd: number;
  response_preview: string;
  created_at: string;
  completed_at: string;
}

// Full run from GET /traces/{id} — enough to re-render a past run read-only.
export interface TraceDetail {
  id: string;
  project_id: string;
  goal: string;
  response_text: string;
  status: string;
  model: string;
  steps: AgentStep[];
  tokens_input: number;
  tokens_output: number;
  tokens_cache_creation: number;
  tokens_cache_read: number;
  cost_usd: number;
  awaiting_approval_plan: unknown;
  approval_reasons: string[];
  created_at: string;
  completed_at: string;
}
