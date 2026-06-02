// Mirrors backend/src/bioforge/tools/sequence/align_msa.py output shape.
// Hand-written; backend Pydantic changes surface here as type errors.

export interface AlignedSequence {
  id: string;
  aligned_sequence: string;
}

export interface AlignMsaOutput {
  method: string;
  num_sequences: number;
  alignment_length: number;
  aligned: AlignedSequence[];
  notes: string[];
  // Provenance fields stamped by execute_tool.
  tool_name?: string;
  tool_version?: string;
  citations?: string[];
}

/** Narrow a tool_output blob to AlignMsaOutput if it looks like one. */
export function isAlignMsaOutput(output: unknown): output is AlignMsaOutput {
  if (!output || typeof output !== "object") return false;
  const o = output as Record<string, unknown>;
  return (
    typeof o.method === "string" &&
    typeof o.alignment_length === "number" &&
    Array.isArray(o.aligned)
  );
}
