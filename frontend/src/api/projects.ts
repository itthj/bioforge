// Thin fetch wrappers for the projects + memory CRUD endpoints. No client-side cache:
// callers refetch after mutations. State is small enough that the cost is negligible
// and consistency is dead simple to reason about.

import type {
  MemoryEntry,
  MemoryUpsert,
  Project,
  ProjectCreate,
  ProjectUpdate,
} from "../types/projects";

class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(`HTTP ${status}: ${detail}`);
    this.status = status;
    this.detail = detail;
  }
}

async function handle<T>(response: Response): Promise<T> {
  if (response.ok) {
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }
  let detail = response.statusText;
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") detail = body.detail;
    else if (Array.isArray(body?.detail)) {
      // FastAPI 422 validation errors come as an array — join the messages.
      detail = body.detail.map((d: { msg?: string }) => d.msg ?? "").join("; ");
    }
  } catch {
    /* fall through with statusText */
  }
  throw new ApiError(response.status, detail);
}

// --- Projects ---------------------------------------------------------------

export async function listProjects(): Promise<Project[]> {
  return handle<Project[]>(await fetch("/projects"));
}

export async function getProject(id: string): Promise<Project> {
  return handle<Project>(await fetch(`/projects/${encodeURIComponent(id)}`));
}

export async function createProject(body: ProjectCreate): Promise<Project> {
  return handle<Project>(
    await fetch("/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function updateProject(
  id: string,
  body: ProjectUpdate,
): Promise<Project> {
  return handle<Project>(
    await fetch(`/projects/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function deleteProject(id: string): Promise<void> {
  return handle<void>(
    await fetch(`/projects/${encodeURIComponent(id)}`, { method: "DELETE" }),
  );
}

// --- Project memory ---------------------------------------------------------

export async function listMemory(projectId: string): Promise<MemoryEntry[]> {
  return handle<MemoryEntry[]>(
    await fetch(`/projects/${encodeURIComponent(projectId)}/memory`),
  );
}

export async function upsertMemory(
  projectId: string,
  key: string,
  body: MemoryUpsert,
): Promise<MemoryEntry> {
  return handle<MemoryEntry>(
    await fetch(
      `/projects/${encodeURIComponent(projectId)}/memory/${encodeURIComponent(key)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    ),
  );
}

export async function deleteMemory(projectId: string, key: string): Promise<void> {
  return handle<void>(
    await fetch(
      `/projects/${encodeURIComponent(projectId)}/memory/${encodeURIComponent(key)}`,
      { method: "DELETE" },
    ),
  );
}

export { ApiError };
