// Mirrors backend/src/bioforge/tools/sequence/score_guide_on_target.py output shape.
// Hand-written; changes to the backend Pydantic models surface here as type errors.

export interface OnTargetScoreBreakdown {
  gc_component: number;
  polyt_component: number;
  position_component: number;
  dinucleotide_component: number;
  component_weights: Record<string, number>;
}

export interface ScoreGuideOnTargetOutput {
  protospacer: string;
  pam: string;
  on_target_score: number;
  score_breakdown: OnTargetScoreBreakdown;
  // Opt-in deep scorers — null unless model=deepcrispr / azimuth_rs2 AND the legacy env is up.
  deepcrispr_on_target_score: number | null;
  deepcrispr_model_version: string | null;
  azimuth_rs2_on_target_score: number | null;
  azimuth_rs2_model_version: string | null;
  caveats: string[];
  // Provenance fields stamped by execute_tool — present on every tool output.
  tool_name?: string;
  tool_version?: string;
  citations?: string[];
}

/** Narrow a tool_output blob to ScoreGuideOnTargetOutput if it looks like one. */
export function isScoreGuideOnTarget(
  output: unknown,
): output is ScoreGuideOnTargetOutput {
  if (!output || typeof output !== "object") return false;
  const o = output as Record<string, unknown>;
  return (
    typeof o.on_target_score === "number" &&
    typeof o.protospacer === "string" &&
    typeof o.score_breakdown === "object" &&
    o.score_breakdown !== null &&
    Array.isArray(o.caveats)
  );
}
