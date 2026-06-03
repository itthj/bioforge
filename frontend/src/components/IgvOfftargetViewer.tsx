import { useEffect, useRef, useState } from "react";
import { coerceOfftargetHits } from "../types/crispr";
import {
  buildOfftargetIgvConfig,
  splitByPlaceability,
} from "./igvOfftargetTrack";

interface IgvOfftargetViewerProps {
  /** The off-target summary's top_hits (loose dicts from the backend). */
  hits: Record<string, unknown>[];
}

type ViewerState = "idle" | "loading" | "ready" | "error";

/**
 * Lazy-loaded igv.js view of off-target hits on the hosted GRCh38 (hg38) genome (Slice B).
 *
 * Honesty gate (enforced in the backend, surfaced here): only hits resolved to a GRCh38
 * primary chromosome (genomic_placement != null) are drawn on hg38. Hits on gene/transcript
 * records, scaffolds, a different build, or a non-human subject are NEVER given a locus —
 * they are listed in the table below with their accession so nothing is silently misplaced.
 *
 * igv loads the hg38 reference from its hosted genome registry (no 3 GB local download).
 * Same lazy-import + graceful-fallback posture as IgvGuideViewer / MolstarViewer.
 */
export function IgvOfftargetViewer({ hits }: IgvOfftargetViewerProps) {
  const typed = coerceOfftargetHits(hits);
  const { placeable, nonPlaceable } = splitByPlaceability(typed);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const browserRef = useRef<unknown>(null);
  const igvRef = useRef<{ removeBrowser?: (b: unknown) => void } | null>(null);
  const [state, setState] = useState<ViewerState>("idle");
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
    };
  }, []);

  async function handleLoad() {
    if (placeable.length === 0 || !containerRef.current) return;
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
      const browser = await igv.createBrowser(
        containerRef.current,
        buildOfftargetIgvConfig(typed),
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
          ? "igv.js viewer not installed. Run `npm install igv` in the frontend/ directory to enable the hg38 genome view. Placed off-targets are listed below."
          : `igv.js failed to render: ${msg}`,
      );
      setState("error");
    }
  }

  return (
    <div className="mt-1">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-medium uppercase tracking-wide text-fg-subtle">
          Off-targets on GRCh38 (hg38)
        </span>
        {placeable.length > 0 && state === "idle" && (
          <button
            type="button"
            onClick={handleLoad}
            className="rounded bg-accent px-2 py-0.5 text-[11px] font-medium text-accent-fg hover:opacity-90"
          >
            Load hg38 browser
          </button>
        )}
        {state === "loading" && (
          <span className="text-[11px] italic text-fg-subtle">Loading hg38…</span>
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

      {placeable.length > 0 ? (
        <div className="mt-1 text-[11px] text-fg-subtle">
          {placeable.length} of {typed.length} hit(s) sit on a GRCh38 chromosome and
          can be shown on hg38 (loaded from igv.js's hosted reference).
        </div>
      ) : (
        <div className="mt-1 rounded border border-border bg-bg px-2 py-1 text-[11px] text-fg-muted">
          None of the {typed.length} returned hit(s) sit on a GRCh38 primary chromosome,
          so there is nothing to place on hg38. See the accessions below.
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
          state === "ready" ? "min-h-[160px]" : "h-0"
        }`}
      />

      {nonPlaceable.length > 0 && (
        <details className="mt-1">
          <summary className="cursor-pointer text-[11px] font-medium text-fg-muted hover:text-fg">
            {nonPlaceable.length} hit(s) not on hg38 (not a GRCh38 chromosome — not placed)
          </summary>
          <ul className="ml-4 mt-1 list-disc space-y-0.5 text-[11px] text-fg-muted">
            {nonPlaceable.map((h, i) => (
              <li key={`${h.accession}-${i}`}>
                <span className="font-mono">{h.accession || "(no accession)"}</span>{" "}
                — {h.risk_label} risk, {h.mismatch_count}mm
                {h.organism ? ` · ${h.organism}` : ""}
                {h.genomic_placement_note && (
                  <span className="block text-fg-subtle">{h.genomic_placement_note}</span>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
