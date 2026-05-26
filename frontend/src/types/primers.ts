// Mirrors backend/src/bioforge/tools/sequence/design_primers.py output shape.
// Hand-written; backend Pydantic changes surface here as type errors.

export interface PrimerPair {
  rank: number;
  forward_sequence: string;
  forward_tm: number;
  forward_gc_percent: number;
  forward_start: number;
  forward_length: number;
  reverse_sequence: string;
  reverse_tm: number;
  reverse_gc_percent: number;
  reverse_start: number;
  reverse_length: number;
  product_size: number;
  pair_penalty: number;
}

export interface DesignPrimersOutput {
  template_length: number;
  target_start: number | null;
  target_end: number | null;
  primer_pairs: PrimerPair[];
  num_returned: number;
  primer3_warnings: string[];
  caveats: string[];
  tool_name?: string;
  tool_version?: string;
  citations?: string[];
}

/** Narrow a tool_output blob to DesignPrimersOutput if it looks like one. */
export function isDesignPrimersOutput(
  output: unknown,
): output is DesignPrimersOutput {
  if (!output || typeof output !== "object") return false;
  const o = output as Record<string, unknown>;
  return (
    typeof o.template_length === "number" &&
    Array.isArray(o.primer_pairs) &&
    Array.isArray(o.primer3_warnings)
  );
}
