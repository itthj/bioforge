import { useEffect, useRef, useState } from "react";
import { listProjects } from "../api/projects";
import type { Project } from "../types/projects";
import { CreateProjectDialog } from "./CreateProjectDialog";

interface ProjectSwitcherProps {
  currentProjectId: string;
  onChange: (project: Project) => void;
  /** Disable switching while an agent run is in progress. */
  disabled?: boolean;
}

export function ProjectSwitcher({
  currentProjectId,
  onChange,
  disabled,
}: ProjectSwitcherProps) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const list = await listProjects();
      setProjects(list);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  // Close the dropdown on outside-click. Mirrors the standard menu pattern; small
  // enough that pulling in headlessui for this single component isn't worth it.
  useEffect(() => {
    if (!open) return;
    function handler(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const current = projects.find((p) => p.id === currentProjectId);

  return (
    <>
      <div ref={containerRef} className="relative">
        <button
          type="button"
          onClick={() => !disabled && setOpen(!open)}
          disabled={disabled}
          className="flex items-center gap-2 rounded-md border border-border bg-surface px-3 py-1.5 text-sm font-medium text-fg-muted shadow-sm hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <span className="font-mono text-xs text-fg-subtle">project:</span>
          <span>{current?.name ?? currentProjectId}</span>
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 20 20"
            fill="currentColor"
            className="h-4 w-4 text-fg-subtle"
          >
            <path
              fillRule="evenodd"
              d="M5.23 7.21a.75.75 0 011.06.02L10 11.06l3.71-3.83a.75.75 0 011.08 1.04l-4.25 4.39a.75.75 0 01-1.08 0L5.21 8.27a.75.75 0 01.02-1.06z"
              clipRule="evenodd"
            />
          </svg>
        </button>

        {open && (
          <div className="absolute right-0 z-40 mt-1 w-72 rounded-md border border-border bg-surface shadow-lg">
            <div className="max-h-72 overflow-auto py-1">
              {loading && (
                <div className="px-3 py-2 text-xs text-fg-subtle">Loading…</div>
              )}
              {error && (
                <div className="px-3 py-2 text-xs text-danger">{error}</div>
              )}
              {!loading && !error && projects.length === 0 && (
                <div className="px-3 py-2 text-xs text-fg-subtle">
                  No projects. Create one below.
                </div>
              )}
              {projects.map((p) => (
                <button
                  type="button"
                  key={p.id}
                  onClick={() => {
                    onChange(p);
                    setOpen(false);
                  }}
                  className={`block w-full px-3 py-2 text-left text-sm hover:bg-surface-2 ${
                    p.id === currentProjectId ? "bg-surface-2" : ""
                  }`}
                >
                  <div className="font-medium text-fg">{p.name}</div>
                  <div className="font-mono text-[11px] text-fg-subtle">{p.id}</div>
                  {(p.organism || p.reference_genome) && (
                    <div className="text-[11px] text-fg-subtle">
                      {[p.organism, p.reference_genome].filter(Boolean).join(" · ")}
                    </div>
                  )}
                </button>
              ))}
            </div>
            <div className="border-t border-border">
              <button
                type="button"
                onClick={() => {
                  setCreating(true);
                  setOpen(false);
                }}
                className="block w-full px-3 py-2 text-left text-sm font-medium text-fg hover:bg-surface-2"
              >
                + New project
              </button>
            </div>
          </div>
        )}
      </div>

      <CreateProjectDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={(p) => {
          // After create, switch to the new project and refresh the list so it appears
          // on the next open.
          setProjects((prev) => [p, ...prev]);
          onChange(p);
        }}
      />
    </>
  );
}
