// Mirrors backend/src/bioforge/tools/structure/fetch_interpro.py output shape.

export interface DomainRegion {
  start: number;
  end: number;
}

export interface InterproDomain {
  interpro_id: string;
  name: string;
  type: string;
  regions: DomainRegion[];
}

export interface FetchInterproOutput {
  uniprot_id: string;
  num_entries: number;
  domains: InterproDomain[];
  caveats: string[];
  tool_name?: string;
  tool_version?: string;
  citations?: string[];
}

/** Narrow tool_output to FetchInterproOutput. */
export function isInterproOutput(
  output: unknown,
): output is FetchInterproOutput {
  if (!output || typeof output !== "object") return false;
  const o = output as Record<string, unknown>;
  return (
    typeof o.uniprot_id === "string" &&
    typeof o.num_entries === "number" &&
    Array.isArray(o.domains)
  );
}
