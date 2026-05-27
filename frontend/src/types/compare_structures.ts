// Mirrors backend/src/bioforge/tools/structure/compare_structures.py output shape.

import type { FetchPdbOutput } from "./pdb_structure";
import type { FetchAlphaFoldOutput } from "./structure";

export interface StructureOverlap {
  experimental_start: number | null;
  experimental_end: number | null;
  alphafold_length: number;
  overlap_start: number | null;
  overlap_end: number | null;
  overlap_residues: number;
  experimental_only_residues: number;
  predicted_only_residues: number;
}

export interface CompareStructuresOutput {
  uniprot_id: string;
  experimental: FetchPdbOutput;
  predicted: FetchAlphaFoldOutput;
  overlap: StructureOverlap;
  summary: string;
  caveats: string[];
  tool_name?: string;
  tool_version?: string;
  citations?: string[];
}

export function isCompareStructuresOutput(
  output: unknown,
): output is CompareStructuresOutput {
  if (!output || typeof output !== "object") return false;
  const o = output as Record<string, unknown>;
  return (
    typeof o.uniprot_id === "string" &&
    typeof o.summary === "string" &&
    o.experimental !== null &&
    typeof o.experimental === "object" &&
    o.predicted !== null &&
    typeof o.predicted === "object" &&
    o.overlap !== null &&
    typeof o.overlap === "object"
  );
}
