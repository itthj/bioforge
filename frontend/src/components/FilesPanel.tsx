import { useCallback, useEffect, useRef, useState } from "react";
import { deleteFile, listFiles, uploadFile, type UploadedFile } from "../api/files";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** "My Data" for a project: upload files, list them, delete. The agent reads them by filename. */
export function FilesPanel({ projectId }: { projectId: string }) {
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      setFiles(await listFiles(projectId));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function handlePick(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      await uploadFile(projectId, file);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  async function handleDelete(id: string) {
    try {
      await deleteFile(projectId, id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-fg">Your data</h2>
          <p className="text-xs text-fg-subtle">
            Upload a FASTA / VCF / table, then ask the agent to use it by name (e.g. “read{" "}
            <span className="text-fg">guides.fasta</span> and design guides for the first sequence”).
          </p>
        </div>
        <label className="shrink-0 cursor-pointer rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-bg shadow-sm transition-opacity hover:opacity-90">
          {busy ? "Uploading…" : "Upload file"}
          <input
            ref={inputRef}
            type="file"
            className="hidden"
            disabled={busy}
            onChange={handlePick}
            accept=".fasta,.fa,.fna,.ffn,.faa,.vcf,.csv,.tsv,.txt,.bed,.gb,.gbk,.genbank"
          />
        </label>
      </div>

      {error && <p className="text-xs text-danger">{error}</p>}

      {files.length === 0 ? (
        <p className="rounded-md border border-dashed border-border px-3 py-6 text-center text-xs text-fg-subtle">
          No files yet. Upload one to analyze your own data.
        </p>
      ) : (
        <ul className="divide-y divide-border overflow-hidden rounded-md border border-border">
          {files.map((f) => (
            <li key={f.id} className="flex items-center justify-between gap-3 bg-surface px-3 py-2">
              <div className="min-w-0">
                <p className="truncate text-sm text-fg">{f.filename}</p>
                <p className="text-xs text-fg-subtle">
                  {formatSize(f.size_bytes)} · {f.sha256.slice(0, 12)}…
                </p>
              </div>
              <button
                type="button"
                onClick={() => handleDelete(f.id)}
                className="shrink-0 rounded-md border border-border px-2 py-1 text-xs text-fg-subtle transition-colors hover:border-danger hover:text-danger"
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
