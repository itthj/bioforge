// Mirrors backend/src/bioforge/tools/structure/fetch_alphafold.py output shape.
// Hand-written; backend Pydantic changes surface here as type errors.

export interface PlddtDistribution {
  very_high: number;
  confident: number;
  low: number;
  very_low: number;
}

export interface FetchAlphaFoldOutput {
  uniprot_id: string;
  entry_id: string;
  organism: string | null;
  gene: string | null;
  uniprot_description: string | null;
  length_residues: number;
  average_plddt: number;
  plddt_distribution: PlddtDistribution;
  pdb_url: string;
  cif_url: string;
  pae_image_url: string | null;
  latest_version: number | null;
  model_created_date: string | null;
  pdb_text: string | null;
  caveats: string[];
  tool_name?: string;
  tool_version?: string;
  citations?: string[];
}

/** Narrow a tool_output blob to FetchAlphaFoldOutput if it looks like one. */
export function isAlphaFoldOutput(
  output: unknown,
): output is FetchAlphaFoldOutput {
  if (!output || typeof output !== "object") return false;
  const o = output as Record<string, unknown>;
  if (typeof o.uniprot_id !== "string") return false;
  if (typeof o.length_residues !== "number") return false;
  if (typeof o.average_plddt !== "number") return false;
  const dist = o.plddt_distribution;
  if (!dist || typeof dist !== "object") return false;
  const d = dist as Record<string, unknown>;
  return (
    typeof d.very_high === "number" &&
    typeof d.confident === "number" &&
    typeof d.low === "number" &&
    typeof d.very_low === "number"
  );
}
