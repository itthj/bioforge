import { useEffect, useRef, useState } from "react";
import type { CrisprEditReportOutput } from "../types/crispr";
import { buildIgvConfig, buildTargetFasta } from "./igvGuideTrack";

interface IgvGuideViewerProps {
  report: CrisprEditReportOutput;
}

type ViewerState = "idle" | "loading" | "ready" | "error" | "missing-sequence";

/**
 * Lazy-loaded igv.js genome browser for CRISPR guides.
 *
 * Renders the SUBMITTED target sequence as its own reference (an inline, non-indexed
 * FASTA) and overlays each candidate guide's protospacer + PAM (+ cut site, when the
 * edit outcome was simulated) at the forward-strand coordinates the tools already emit.
 * There is deliberately NO genome build here: the coordinates are sequence-relative to
 * the submitted locus only, so the view cannot misplace a guide on a chromosome.
 *
 * igv.js is ~3 MB, so we dynamically `import()` it on the first "Load" click — the app
 * bundle stays small and still works if igv isn't installed (the catch path renders an
 * install hint). Same posture as MolstarViewer.
 */
export function IgvGuideViewer({ report }: IgvGuideViewerProps) {
  const sequence = report.target_sequence ?? "";
  const containerRef = useRef<HTMLDivElement | null>(null);
  const browserRef = useRef<unknown>(null);
  const igvRef = useRef<{ removeBrowser?: (b: unknown) => void } | null>(null);
  const objectUrlRef = useRef<string | null>(null);
  const [state, setState] = useState<ViewerState>(
    sequence ? "idle" : "missing-sequence",
  );
  const [errorMsg, setErrorMsg] = useState<string>("");

  useEffect(() => {
    return () => {
      const browser = browserRef.current;
      const igv = igvRef.current;
      try {
        if (browser && igv && typeof igv.removeBrowser === "function") {
          igv.removeBrowser(browser);
        } else if (
          browser &&
          typeof (browser as { dispose?: () => void }).dispose === "function"
        ) {
          (browser as { dispose: () => void }).dispose();
        }
      } catch {
        // Disposal failures during unmount shouldn't blow up React.
      }
      browserRef.current = null;
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = null;
      }
    };
  }, []);

  async function handleLoad() {
    if (!sequence || !containerRef.current) return;
    setState("loading");
    setErrorMsg("");
    try {
      const mod = (await import(/* @vite-ignore */ "igv")) as {
        default: {
          createBrowser: (el: HTMLElement, config: unknown) => Promise<unknown>;
          removeBrowser?: (b: unknown) => void;
        };
      };
      const igv = mod.default;
      igvRef.current = igv;

      const fasta = buildTargetFasta(sequence);
      const url = URL.createObjectURL(new Blob([fasta], { type: "text/plain" }));
      objectUrlRef.current = url;

      const browser = await igv.createBrowser(
        containerRef.current,
        buildIgvConfig(report, url),
      );
      browserRef.current = browser;
      setState("ready");
    } catch (err) {
      const msg =
        err && typeof err === "object" && "message" in err
          ? String((err as { message: unknown }).message)
          : String(err);
      const looksLikeMissingPackage =
        msg.includes("Failed to resolve module") ||
        msg.includes("Cannot find module") ||
        msg.includes("MODULE_NOT_FOUND") ||
        msg.toLowerCase().includes("igv");
      setErrorMsg(
        looksLikeMissingPackage
          ? "igv.js viewer not installed. Run `npm install igv` in the frontend/ directory to enable the genome-browser view. Guide coordinates are listed in the report below."
          : `igv.js failed to render: ${msg}`,
      );
      setState("error");
    }
  }

  if (state === "missing-sequence") {
    return (
      <div className="rounded border border-border bg-surface p-2 text-xs text-fg-muted">
        Genome-browser view unavailable: this report carries no target sequence.
      </div>
    );
  }

  const guideCount = report.guides.length;

  return (
    <div>
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-medium uppercase tracking-wide text-fg-subtle">
          Guide map (submitted sequence)
        </span>
        {state === "idle" && (
          <button
            type="button"
            onClick={handleLoad}
            className="rounded bg-accent px-2 py-0.5 text-[11px] font-medium text-accent-fg hover:opacity-90"
          >
            Load genome browser
          </button>
        )}
        {state === "loading" && (
          <span className="text-[11px] italic text-fg-subtle">Loading igv.js…</span>
        )}
        {state === "ready" && (
          <span className="text-[11px] italic text-success">Loaded</span>
        )}
        {state === "error" && (
          <button
            type="button"
            onClick={handleLoad}
            className="text-[11px] text-danger underline"
          >
            Retry
          </button>
        )}
      </div>
      {state === "idle" && (
        <div className="mt-1 text-[11px] text-fg-subtle">
          {report.target_length} nt target · {guideCount}{" "}
          {guideCount === 1 ? "guide" : "guides"} · protospacer + PAM
          {report.guides.some((g) => g.edit_outcome_summary) ? " + cut site" : ""}.
          Coordinates are relative to the submitted sequence — not a genome build.
        </div>
      )}
      {state === "error" && (
        <div className="mt-1 rounded border border-border bg-surface-2 px-2 py-1 text-[11px] text-danger">
          {errorMsg}
        </div>
      )}
      <div
        ref={containerRef}
        className={`mt-1 rounded border border-border bg-surface ${
          state === "ready" ? "min-h-[180px]" : "h-0"
        }`}
      />
    </div>
  );
}
