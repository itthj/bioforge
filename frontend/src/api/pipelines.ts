export interface PipelineJob {
  id: string;
  project_id: string;
  pipeline: string;
  revision: string;
  profile: string;
  status: string;
  events: PipelineEvent[];
  error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface PipelineEvent {
  seq: number;
  type: string;
  step_name: string | null;
  payload: Record<string, unknown> | null;
  ts: string;
}

export interface PipelineSubmitRequest {
  project_id: string;
  pipeline: string;
  revision?: string;
  profile: string;
  samplesheet: string;
  params?: Record<string, string>;
}

export async function submitPipeline(req: PipelineSubmitRequest): Promise<PipelineJob> {
  const res = await fetch("/pipelines", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "Submit failed");
  }
  return res.json();
}

export async function listPipelines(projectId: string): Promise<PipelineJob[]> {
  const res = await fetch(`/pipelines?project_id=${encodeURIComponent(projectId)}`);
  if (!res.ok) throw new Error("Failed to list pipelines");
  return res.json();
}

export async function getPipeline(jobId: string): Promise<PipelineJob> {
  const res = await fetch(`/pipelines/${jobId}`);
  if (!res.ok) throw new Error("Pipeline job not found");
  return res.json();
}

export async function cancelPipeline(jobId: string): Promise<void> {
  await fetch(`/pipelines/${jobId}`, { method: "DELETE" });
}

export async function fetchSupportedPipelines(): Promise<Record<string, string>> {
  const res = await fetch("/pipelines/catalogue/supported");
  if (!res.ok) return {};
  return res.json();
}
