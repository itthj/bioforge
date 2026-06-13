export interface UploadedFile {
  id: string;
  project_id: string;
  filename: string;
  content_type: string | null;
  size_bytes: number;
  sha256: string;
  created_at: string;
}

async function _detail(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
    if (Array.isArray(body?.detail)) return body.detail.map((d: { msg?: string }) => d.msg ?? "").join("; ");
  } catch {
    /* fall through */
  }
  return fallback;
}

export async function listFiles(projectId: string): Promise<UploadedFile[]> {
  const res = await fetch(`/projects/${encodeURIComponent(projectId)}/files`);
  if (!res.ok) throw new Error(await _detail(res, `Failed to list files (HTTP ${res.status})`));
  return res.json() as Promise<UploadedFile[]>;
}

export async function uploadFile(projectId: string, file: File): Promise<UploadedFile> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`/projects/${encodeURIComponent(projectId)}/files`, { method: "POST", body: form });
  if (!res.ok) throw new Error(await _detail(res, `Upload failed (HTTP ${res.status})`));
  return res.json() as Promise<UploadedFile>;
}

export async function deleteFile(projectId: string, fileId: string): Promise<void> {
  const res = await fetch(
    `/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileId)}`,
    { method: "DELETE" },
  );
  if (!res.ok && res.status !== 404) throw new Error(await _detail(res, `Delete failed (HTTP ${res.status})`));
}
