// Mirrors the Pydantic shapes in backend/src/bioforge/api/projects.py.
// Kept hand-written so changes to the backend show up as type errors here rather
// than silent runtime mismatches.

export interface Project {
  id: string;
  name: string;
  description: string | null;
  organism: string | null;
  reference_genome: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectCreate {
  id: string;
  name: string;
  description?: string;
  organism?: string;
  reference_genome?: string;
}

export interface ProjectUpdate {
  name?: string;
  description?: string;
  organism?: string;
  reference_genome?: string;
}

export type MemoryKind = "fact" | "preference" | "summary" | "file_reference";
export type MemorySource = "agent" | "user" | "system";

export interface MemoryEntry {
  key: string;
  value: string;
  kind: MemoryKind;
  source: MemorySource;
  rationale: string | null;
  created_at: string;
  updated_at: string;
}

export interface MemoryUpsert {
  value: string;
  kind: MemoryKind;
  rationale?: string;
}
