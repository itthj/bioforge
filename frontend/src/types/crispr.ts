// Mirrors backend/src/bioforge/tools/sequence/crispr_edit_report.py output shape.
// Hand-written; changes to the backend Pydantic models surface here as type errors.

export type RecommendationLabel = "preferred" | "acceptable" | "caution" | "avoid";

export interface OutcomeSummary {
  cut_position_fwd: number;
  frameshift_probability: number;
  no_edit_probability: number;
  // The backend emits each outcome as a free-form dict matching edit_outcome's row
  // shape; we keep it loose here rather than re-mirroring every field.
  top_outcomes: Record<string, unknown>[];
}

export interface OfftargetSummary {
  searched: boolean;
  database: string | null;
  high_risk_count: number;
  medium_risk_count: number;
  low_risk_count: number;
  top_hits: Record<string, unknown>[];
  caveats: string[];
}

export interface GuideReport {
  rank: number;
  protospacer: string;
  pam_sequence: string;
  strand: "+" | "-";
  protospacer_start: number;
  protospacer_end: number;
  pam_start: number;
  pam_end: number;
  heuristic_score: number;
  on_target_score: number | null;
  recommendation_score: number;
  recommendation_label: RecommendationLabel;
  rationale: string[];
  off_target_summary: OfftargetSummary;
  edit_outcome_summary: OutcomeSummary | null;
}

export interface CrisprEditReportOutput {
  target_length: number;
  pam: string;
  num_guides_considered: number;
  recommended_guide: GuideReport | null;
  guides: GuideReport[];
  tool_chain: string[];
  caveats: string[];
  // Provenance fields stamped by execute_tool — present on every tool output.
  tool_name?: string;
  tool_version?: string;
  citations?: string[];
}

/** Narrow a tool_output blob to CrisprEditReportOutput if it looks like one. */
export function isCrisprEditReport(
  output: unknown,
): output is CrisprEditReportOutput {
  if (!output || typeof output !== "object") return false;
  const o = output as Record<string, unknown>;
  return (
    typeof o.target_length === "number" &&
    typeof o.pam === "string" &&
    Array.isArray(o.guides) &&
    Array.isArray(o.tool_chain)
  );
}
