// Mirrors backend/src/bioforge/tools/structure/find_best.py output shape.

import type { FetchPdbOutput } from "./pdb_structure";
import type { FetchAlphaFoldOutput } from "./structure";

export interface ExperimentalCandidate {
  pdb_id: string;
  chain_id: string | null;
  coverage: number | null;
  resolution_angstrom: number | null;
  experimental_method: string | null;
  unp_start: number | null;
  unp_end: number | null;
}

export interface FindBestStructureOutput {
  uniprot_id: string;
  source: "experimental" | "predicted";
  reason: string;
  experimental_candidates: ExperimentalCandidate[];
  pdb_result: FetchPdbOutput | null;
  alphafold_result: FetchAlphaFoldOutput | null;
  caveats: string[];
  tool_name?: string;
  tool_version?: string;
  citations?: string[];
}

/** Narrow a tool_output blob to FindBestStructureOutput if it looks like one. */
export function isFindBestStructureOutput(
  output: unknown,
): output is FindBestStructureOutput {
  if (!output || typeof output !== "object") return false;
  const o = output as Record<string, unknown>;
  return (
    typeof o.uniprot_id === "string" &&
    (o.source === "experimental" || o.source === "predicted") &&
    typeof o.reason === "string" &&
    Array.isArray(o.experimental_candidates) &&
    Array.isArray(o.caveats)
  );
}
