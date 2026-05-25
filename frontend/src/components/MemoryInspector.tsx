import { useEffect, useState } from "react";
import {
  ApiError,
  deleteMemory,
  listMemory,
  upsertMemory,
} from "../api/projects";
import type { MemoryEntry, MemoryKind, MemorySource } from "../types/projects";

interface MemoryInspectorProps {
  projectId: string;
}

const KIND_LABELS: Record<MemoryKind, string> = {
  fact: "fact",
  preference: "preference",
  summary: "summary",
  file_reference: "file ref",
};

const SOURCE_STYLES: Record<MemorySource, string> = {
  agent: "bg-emerald-100 text-emerald-800",
  user: "bg-blue-100 text-blue-800",
  system: "bg-slate-200 text-slate-700",
};

export function MemoryInspector({ projectId }: MemoryInspectorProps) {
  const [entries, setEntries] = useState<MemoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const list = await listMemory(projectId);
      setEntries(list);
    } catch (e) {
      setError(
        e instanceof ApiError
          ? `${e.status}: ${e.detail}`
          : e instanceof Error
            ? e.message
            : String(e),
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    setEditingKey(null);
  }, [projectId]);

  async function handleSave(key: string, body: MemoryEntry) {
    try {
      const updated = await upsertMemory(projectId, key, {
        value: body.value,
        kind: body.kind,
        rationale: body.rationale ?? undefined,
      });
      setEntries((prev) => {
        const exists = prev.some((e) => e.key === updated.key);
        return exists
          ? prev.map((e) => (e.key === updated.key ? updated : e))
          : [updated, ...prev];
      });
      setEditingKey(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function handleDelete(key: string) {
    if (!confirm(`Delete memory entry "${key}"? This can't be undone.`)) return;
    try {
      await deleteMemory(projectId, key);
      setEntries((prev) => prev.filter((e) => e.key !== key));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Project memory
          </h2>
          <p className="mt-0.5 text-xs text-slate-400">
            Facts the agent has learned, plus anything you've added. Agent writes go
            through the <code className="font-mono">remember</code> tool; user edits
            (here) are tagged <span className="font-mono">source=user</span>.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setAddOpen(true)}
          className="rounded-md bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800"
        >
          + Add entry
        </button>
      </div>

      {error && (
        <div className="rounded border border-rose-300 bg-rose-50 p-2 text-xs text-rose-800">
          {error}
        </div>
      )}

      {loading ? (
        <div className="rounded-md border border-dashed border-slate-300 bg-white p-6 text-center text-sm text-slate-400">
          Loading memory…
        </div>
      ) : entries.length === 0 ? (
        <div className="rounded-md border border-dashed border-slate-300 bg-white p-6 text-center text-sm text-slate-400">
          No memory entries yet. The agent will add them as it learns durable facts,
          or click "+ Add entry" to add one manually.
        </div>
      ) : (
        <ul className="space-y-2">
          {entries.map((entry) => (
            <li key={entry.key}>
              {editingKey === entry.key ? (
                <MemoryEditor
                  initial={entry}
                  onCancel={() => setEditingKey(null)}
                  onSave={(updated) => handleSave(entry.key, updated)}
                />
              ) : (
                <MemoryRow
                  entry={entry}
                  onEdit={() => setEditingKey(entry.key)}
                  onDelete={() => handleDelete(entry.key)}
                />
              )}
            </li>
          ))}
        </ul>
      )}

      <AddMemoryDialog
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onCreated={(entry) => {
          setEntries((prev) => [entry, ...prev.filter((e) => e.key !== entry.key)]);
          setAddOpen(false);
        }}
        projectId={projectId}
      />
    </div>
  );
}

function MemoryRow({
  entry,
  onEdit,
  onDelete,
}: {
  entry: MemoryEntry;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="rounded-md border border-slate-200 bg-white p-3 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm font-semibold text-slate-900">
              {entry.key}
            </span>
            <Badge text={KIND_LABELS[entry.kind]} classes="bg-slate-100 text-slate-700" />
            <Badge text={entry.source} classes={SOURCE_STYLES[entry.source]} />
          </div>
          <div className="mt-2 whitespace-pre-wrap text-sm text-slate-800">
            {entry.value}
          </div>
          {entry.rationale && (
            <div className="mt-2 text-xs italic text-slate-500">
              why: {entry.rationale}
            </div>
          )}
          <div className="mt-2 font-mono text-[11px] text-slate-400">
            updated {new Date(entry.updated_at).toLocaleString()}
          </div>
        </div>
        <div className="flex shrink-0 flex-col gap-1">
          <button
            type="button"
            onClick={onEdit}
            className="rounded border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
          >
            Edit
          </button>
          <button
            type="button"
            onClick={onDelete}
            className="rounded border border-rose-300 bg-white px-2 py-1 text-xs font-medium text-rose-700 hover:bg-rose-50"
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  );
}

function MemoryEditor({
  initial,
  onCancel,
  onSave,
}: {
  initial: MemoryEntry;
  onCancel: () => void;
  onSave: (entry: MemoryEntry) => void;
}) {
  const [value, setValue] = useState(initial.value);
  const [kind, setKind] = useState<MemoryKind>(initial.kind);
  const [rationale, setRationale] = useState(initial.rationale ?? "");

  return (
    <div className="rounded-md border border-slate-300 bg-amber-50 p-3 shadow-sm">
      <div className="mb-2 flex items-center gap-2">
        <span className="font-mono text-sm font-semibold text-slate-900">
          {initial.key}
        </span>
        <span className="text-xs italic text-slate-500">(editing)</span>
      </div>
      <textarea
        value={value}
        onChange={(e) => setValue(e.target.value)}
        rows={3}
        className="w-full rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500"
      />
      <div className="mt-2 grid grid-cols-2 gap-2">
        <label className="block text-xs">
          <span className="mb-1 block font-medium text-slate-700">Kind</span>
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as MemoryKind)}
            className="w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
          >
            <option value="fact">fact</option>
            <option value="preference">preference</option>
            <option value="summary">summary</option>
            <option value="file_reference">file_reference</option>
          </select>
        </label>
      </div>
      <label className="mt-2 block text-xs">
        <span className="mb-1 block font-medium text-slate-700">Rationale</span>
        <input
          type="text"
          value={rationale}
          onChange={(e) => setRationale(e.target.value)}
          placeholder="Why this is worth remembering"
          className="w-full rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm"
        />
      </label>
      <div className="mt-3 flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() =>
            onSave({
              ...initial,
              value,
              kind,
              rationale: rationale.trim() || null,
            })
          }
          disabled={!value.trim()}
          className="rounded-md bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800 disabled:bg-slate-400"
        >
          Save
        </button>
      </div>
    </div>
  );
}

function AddMemoryDialog({
  open,
  onClose,
  onCreated,
  projectId,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (entry: MemoryEntry) => void;
  projectId: string;
}) {
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [kind, setKind] = useState<MemoryKind>("preference");
  const [rationale, setRationale] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  const keyValid = /^[a-zA-Z0-9_-]+$/.test(key) && key.length > 0;
  const canSubmit = keyValid && value.trim().length > 0 && !submitting;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const entry = await upsertMemory(projectId, key, {
        value: value.trim(),
        kind,
        rationale: rationale.trim() || undefined,
      });
      onCreated(entry);
      setKey("");
      setValue("");
      setRationale("");
      setKind("preference");
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-slate-900/40 p-4 pt-20">
      <div className="w-full max-w-md rounded-lg border border-slate-200 bg-white p-5 shadow-xl">
        <div className="flex items-start justify-between">
          <h2 className="text-base font-semibold text-slate-900">Add memory entry</h2>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="-mr-1 text-slate-400 hover:text-slate-700 disabled:opacity-50"
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <p className="mt-1 text-xs text-slate-500">
          Writing here is tagged{" "}
          <span className="font-mono">source=user</span> in the audit trail.
        </p>

        <form onSubmit={handleSubmit} className="mt-4 space-y-3">
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-slate-700">
              Key<span className="ml-0.5 text-rose-500">*</span>
            </span>
            <input
              type="text"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="preferred_organism"
              className="w-full rounded-md border border-slate-300 px-3 py-1.5 font-mono text-sm"
              autoFocus
              disabled={submitting}
            />
            {key.length > 0 && !keyValid && (
              <div className="mt-1 text-xs text-rose-600">
                Letters, digits, underscores, dashes only.
              </div>
            )}
          </label>

          <label className="block">
            <span className="mb-1 block text-xs font-medium text-slate-700">
              Value<span className="ml-0.5 text-rose-500">*</span>
            </span>
            <textarea
              value={value}
              onChange={(e) => setValue(e.target.value)}
              rows={3}
              className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm"
              disabled={submitting}
            />
          </label>

          <label className="block">
            <span className="mb-1 block text-xs font-medium text-slate-700">Kind</span>
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value as MemoryKind)}
              className="w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
              disabled={submitting}
            >
              <option value="fact">fact</option>
              <option value="preference">preference</option>
              <option value="summary">summary</option>
              <option value="file_reference">file_reference</option>
            </select>
          </label>

          <label className="block">
            <span className="mb-1 block text-xs font-medium text-slate-700">
              Rationale
            </span>
            <input
              type="text"
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
              placeholder="Why this is worth remembering"
              className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm"
              disabled={submitting}
            />
          </label>

          {error && (
            <div className="rounded border border-rose-300 bg-rose-50 p-2 text-xs text-rose-800">
              {error}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!canSubmit}
              className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-400"
            >
              {submitting ? "Saving…" : "Save"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function Badge({ text, classes }: { text: string; classes: string }) {
  return (
    <span
      className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium ${classes}`}
    >
      {text}
    </span>
  );
}
