// Mirrors backend/src/bioforge/tools/structure/fetch_pdb.py output shape.
// Hand-written; backend Pydantic changes surface here as type errors.

export interface FetchPdbOutput {
  pdb_id: string;
  title: string | null;
  experimental_method: string | null;
  resolution_angstrom: number | null;
  deposit_date: string | null;
  release_date: string | null;
  revision_date: string | null;
  keywords: string | null;
  chain_ids: string[];
  num_chains: number;
  num_residues: number;
  residues_per_chain: Record<string, number>;
  ligand_ids: string[];
  mean_b_factor: number | null;
  pdb_url: string;
  cif_url: string;
  pdb_text: string | null;
  caveats: string[];
  tool_name?: string;
  tool_version?: string;
  citations?: string[];
}

/** Narrow a tool_output blob to FetchPdbOutput if it looks like one. */
export function isPdbStructureOutput(
  output: unknown,
): output is FetchPdbOutput {
  if (!output || typeof output !== "object") return false;
  const o = output as Record<string, unknown>;
  return (
    typeof o.pdb_id === "string" &&
    typeof o.num_chains === "number" &&
    typeof o.num_residues === "number" &&
    Array.isArray(o.chain_ids) &&
    Array.isArray(o.ligand_ids) &&
    Array.isArray(o.caveats)
  );
}
