/**
 * MethodsModal
 * ============
 * A "Draft methods section" button that opens an inline modal with three tabs:
 *   1. Paragraph   — manuscript-ready past-tense prose with inline citations
 *   2. BibTeX      — copy-paste citation block for any reference manager
 *   3. Parameters  — Supplementary Methods parameter table (Markdown)
 *
 * The backend at GET /traces/{traceId}/methods-draft returns JSON with these
 * three components, generated from the run manifest with hardcoded benchmark
 * accuracy numbers and grounding-validated LLM prose polish.
 *
 * Mount at the bottom of FinalCard:
 *   <MethodsModal traceId={done.trace_id} />
 */

import { useState, useCallback, useEffect, useRef } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface MethodsDraftResponse {
  paragraph: string;
  bibtex_block: string;
  param_table_md: string;
  warnings: string[];
  trace_id: string;
}

type Tab = "paragraph" | "bibtex" | "parameters";

interface MethodsModalProps {
  traceId: string;
}

// ---------------------------------------------------------------------------
// Copy hook
// ---------------------------------------------------------------------------

function useCopy() {
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const copy = useCallback((text: string, key: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopiedKey(key);
      setTimeout(() => setCopiedKey(null), 2000);
    });
  }, []);
  return { copy, copiedKey };
}

// ---------------------------------------------------------------------------
// API fetch
// ---------------------------------------------------------------------------

async function fetchMethodsDraft(traceId: string, polish = true): Promise<MethodsDraftResponse> {
  const res = await fetch(`/traces/${traceId}/methods-draft?polish=${polish}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return res.json() as Promise<MethodsDraftResponse>;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function MethodsModal({ traceId }: MethodsModalProps) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<Tab>("paragraph");
  const [draft, setDraft] = useState<MethodsDraftResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const { copy, copiedKey } = useCopy();

  // Load draft when modal opens (lazy — only fetch once)
  const openModal = useCallback(async () => {
    setOpen(true);
    if (draft) return;
    setLoading(true);
    setError(null);
    try {
      const d = await fetchMethodsDraft(traceId);
      setDraft(d);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [draft, traceId]);

  const closeModal = useCallback(() => setOpen(false), []);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeModal();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, closeModal]);

  // Focus trap — keep focus inside dialog
  useEffect(() => {
    if (open && dialogRef.current) {
      dialogRef.current.focus();
    }
  }, [open]);

  const activeText = !draft
    ? ""
    : tab === "paragraph"
    ? draft.paragraph
    : tab === "bibtex"
    ? draft.bibtex_block
    : draft.param_table_md;

  const paragraphPlusCites =
    draft
      ? `${draft.paragraph}\n\n${draft.bibtex_block}`
      : "";

  const TAB_LABELS: Record<Tab, string> = {
    paragraph: "Paragraph",
    bibtex: "BibTeX",
    parameters: "Parameters",
  };

  return (
    <>
      {/* Trigger — styled to match the existing provenance footer links */}
      <button
        type="button"
        onClick={openModal}
        className="text-accent hover:underline text-[11px] font-medium rounded"
        title="Generate a manuscript-ready methods paragraph for this run"
      >
        Draft methods ✦
      </button>

      {/* Modal */}
      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: "rgba(0,0,0,0.55)" }}
          onClick={(e) => {
            if (e.target === e.currentTarget) closeModal();
          }}
          role="dialog"
          aria-modal="true"
          aria-label="Methods section draft"
        >
          <div
            ref={dialogRef}
            tabIndex={-1}
            className="flex flex-col w-full max-w-2xl max-h-[80vh] rounded-lg border border-border bg-surface shadow-xl outline-none"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center justify-between border-b border-border px-4 py-3 flex-shrink-0">
              <span className="text-sm font-medium text-fg">
                Methods section draft
              </span>
              <div className="flex items-center gap-2">
                {draft && !loading && (
                  <span className="text-[10px] font-mono text-fg-subtle">
                    trace: {traceId.slice(0, 8)}
                  </span>
                )}
                <button
                  type="button"
                  onClick={closeModal}
                  className="rounded p-1 text-fg-subtle hover:bg-surface-2 hover:text-fg"
                  aria-label="Close"
                >
                  ✕
                </button>
              </div>
            </div>

            {/* Tab bar */}
            <div
              className="flex border-b border-border px-4 flex-shrink-0"
              role="tablist"
            >
              {(["paragraph", "bibtex", "parameters"] as Tab[]).map((t) => (
                <button
                  key={t}
                  role="tab"
                  aria-selected={tab === t}
                  type="button"
                  onClick={() => setTab(t)}
                  className={[
                    "px-3 py-2 text-xs font-medium border-b-2 -mb-px transition-colors",
                    tab === t
                      ? "border-accent text-accent"
                      : "border-transparent text-fg-muted hover:text-fg",
                  ].join(" ")}
                >
                  {TAB_LABELS[t]}
                </button>
              ))}
            </div>

            {/* Body */}
            <div className="flex-1 overflow-y-auto px-4 py-3 min-h-0">
              {/* Loading */}
              {loading && (
                <div className="flex items-center gap-2 py-6 text-sm text-fg-muted">
                  <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-border border-t-accent" />
                  Generating…
                </div>
              )}

              {/* Error */}
              {error && !loading && (
                <div className="rounded border border-danger/40 bg-danger/10 p-3 text-xs text-danger">
                  <strong>Generation failed:</strong> {error}
                </div>
              )}

              {/* Warnings */}
              {draft && !loading && draft.warnings.length > 0 && (
                <div className="mb-3 rounded border border-warn/40 bg-warn/10 p-2">
                  {draft.warnings.map((w, i) => (
                    <p key={i} className="text-[11px] text-warn">
                      ⚠ {w}
                    </p>
                  ))}
                </div>
              )}

              {/* Content */}
              {draft && !loading && (
                <>
                  {tab === "paragraph" && (
                    <p className="text-sm text-fg leading-7 whitespace-pre-wrap font-serif">
                      {draft.paragraph}
                    </p>
                  )}
                  {tab === "bibtex" && (
                    <pre className="text-[11px] text-fg-muted font-mono whitespace-pre-wrap break-words leading-5">
                      {draft.bibtex_block}
                    </pre>
                  )}
                  {tab === "parameters" && (
                    <pre className="text-[11px] text-fg-muted font-mono whitespace-pre-wrap break-words leading-5">
                      {draft.param_table_md}
                    </pre>
                  )}
                </>
              )}
            </div>

            {/* Footer */}
            {draft && !loading && (
              <div className="flex flex-wrap items-center gap-2 border-t border-border px-4 py-2 flex-shrink-0">
                <button
                  type="button"
                  onClick={() => copy(activeText, "active")}
                  className="rounded border border-border bg-surface-2 px-3 py-1 text-xs text-fg-muted hover:text-fg"
                >
                  {copiedKey === "active" ? "✓ Copied" : `Copy ${TAB_LABELS[tab].toLowerCase()}`}
                </button>
                {tab === "paragraph" && (
                  <button
                    type="button"
                    onClick={() => copy(paragraphPlusCites, "full")}
                    className="rounded border border-border bg-surface-2 px-3 py-1 text-xs text-fg-muted hover:text-fg"
                  >
                    {copiedKey === "full" ? "✓ Copied" : "Copy paragraph + BibTeX"}
                  </button>
                )}
                <span className="ml-auto text-[10px] text-fg-subtle">
                  Benchmark numbers are hardcoded constants, not LLM-generated
                </span>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
