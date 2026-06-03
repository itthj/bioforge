import { useState } from "react";
import { ApiError, createProject } from "../api/projects";
import type { Project } from "../types/projects";

interface CreateProjectDialogProps {
  open: boolean;
  onClose: () => void;
  onCreated: (project: Project) => void;
}

const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export function CreateProjectDialog({
  open,
  onClose,
  onCreated,
}: CreateProjectDialogProps) {
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [organism, setOrganism] = useState("");
  const [referenceGenome, setReferenceGenome] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  const slugValid = SLUG_RE.test(id);
  const canSubmit = slugValid && name.trim().length > 0 && !submitting;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const project = await createProject({
        id,
        name: name.trim(),
        description: description.trim() || undefined,
        organism: organism.trim() || undefined,
        reference_genome: referenceGenome.trim() || undefined,
      });
      onCreated(project);
      reset();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  function reset() {
    setId("");
    setName("");
    setOrganism("");
    setReferenceGenome("");
    setDescription("");
    setError(null);
  }

  function handleClose() {
    if (submitting) return;
    reset();
    onClose();
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-slate-900/40 p-4 pt-20">
      <div
        className="w-full max-w-md rounded-lg border border-border bg-surface p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between">
          <h2 className="text-base font-semibold text-fg">
            Create project
          </h2>
          <button
            type="button"
            onClick={handleClose}
            disabled={submitting}
            className="-mr-1 text-fg-subtle hover:text-fg-muted disabled:opacity-50"
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <p className="mt-1 text-xs text-fg-subtle">
          The project id is permanent — pick something durable. Lowercase
          letters, digits, dashes.
        </p>

        <form onSubmit={handleSubmit} className="mt-4 space-y-3">
          <Field label="Project id" required>
            <input
              type="text"
              value={id}
              onChange={(e) => setId(e.target.value)}
              placeholder="crispr-screen-2026"
              className="w-full rounded-md border border-border px-3 py-1.5 font-mono text-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              disabled={submitting}
              autoFocus
            />
            {id.length > 0 && !slugValid && (
              <div className="mt-1 text-xs text-danger">
                Lowercase letters, digits, single dashes between segments.
              </div>
            )}
          </Field>

          <Field label="Name" required>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="CRISPR screen 2026"
              className="w-full rounded-md border border-border px-3 py-1.5 text-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              disabled={submitting}
            />
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Organism">
              <input
                type="text"
                value={organism}
                onChange={(e) => setOrganism(e.target.value)}
                placeholder="Homo sapiens"
                className="w-full rounded-md border border-border px-3 py-1.5 text-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
                disabled={submitting}
              />
            </Field>
            <Field label="Reference genome">
              <input
                type="text"
                value={referenceGenome}
                onChange={(e) => setReferenceGenome(e.target.value)}
                placeholder="GRCh38"
                className="w-full rounded-md border border-border px-3 py-1.5 text-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
                disabled={submitting}
              />
            </Field>
          </div>

          <Field label="Description">
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              className="w-full rounded-md border border-border px-3 py-1.5 text-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              disabled={submitting}
            />
          </Field>

          {error && (
            <div className="rounded border border-border bg-surface-2 p-2 text-xs text-danger">
              {error}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={handleClose}
              disabled={submitting}
              className="rounded-md border border-border bg-surface px-3 py-1.5 text-sm font-medium text-fg-muted hover:bg-surface-2 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!canSubmit}
              className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting ? "Creating…" : "Create"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function Field({
  label,
  children,
  required,
}: {
  label: string;
  children: React.ReactNode;
  required?: boolean;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-fg-muted">
        {label}
        {required && <span className="ml-0.5 text-rose-500">*</span>}
      </span>
      {children}
    </label>
  );
}
