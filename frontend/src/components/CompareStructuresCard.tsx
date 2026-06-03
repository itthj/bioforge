import { useState } from "react";

import type { CompareStructuresOutput } from "../types/compare_structures";
import { PdbStructureCard } from "./PdbStructureCard";
import { StructureCard } from "./StructureCard";

interface CompareStructuresCardProps {
  result: CompareStructuresOutput;
}

type Tab = "side-by-side" | "experimental" | "predicted";

/**
 * Renders the compare_structures output.
 *
 * Top panel: summary banner + overlap visualization (a stacked horizontal bar
 * showing experimental-only / overlap / predicted-only regions along the
 * AlphaFold sequence).
 *
 * Body: tabbed view of the two structures. "Side-by-side" shows both in two
 * columns; individual tabs zoom into one structure card at a time. We don't
 * try to load Mol* twice in the same viewport — that's a memory hazard;
 * users switch tabs and each card lazy-loads its own Mol* instance.
 */
export function CompareStructuresCard({ result }: CompareStructuresCardProps) {
  const [tab, setTab] = useState<Tab>("side-by-side");
  const overlap = result.overlap;
  const af_len = overlap.alphafold_length || 1;

  // Build the overlap visualization segments.
  const segments: { left: number; width: number; color: string; label: string }[] = [];
  if (overlap.overlap_start !== null && overlap.overlap_end !== null) {
    // Predicted-only region BEFORE the overlap.
    if (overlap.overlap_start > 1) {
      segments.push({
        left: 0,
        width: ((overlap.overlap_start - 1) / af_len) * 100,
        color: "bg-purple-400",
        label: `Prediction-only (1-${overlap.overlap_start - 1})`,
      });
    }
    // Overlap region.
    segments.push({
      left: ((overlap.overlap_start - 1) / af_len) * 100,
      width: ((overlap.overlap_end - overlap.overlap_start + 1) / af_len) * 100,
      color: "bg-emerald-500",
      label: `Validated overlap (${overlap.overlap_start}-${overlap.overlap_end})`,
    });
    // Predicted-only region AFTER the overlap.
    if (overlap.overlap_end < af_len) {
      segments.push({
        left: (overlap.overlap_end / af_len) * 100,
        width: ((af_len - overlap.overlap_end) / af_len) * 100,
        color: "bg-purple-400",
        label: `Prediction-only (${overlap.overlap_end + 1}-${af_len})`,
      });
    }
  } else {
    // No overlap — the whole AlphaFold model is prediction-only.
    segments.push({
      left: 0,
      width: 100,
      color: "bg-purple-400",
      label: `Prediction-only (full ${af_len} residues — no SIFTS overlap)`,
    });
  }

  return (
    <div className="space-y-3">
      {/* Banner */}
      <div className="rounded-md border border-border bg-surface-2 p-2 text-xs text-success">
        <div className="flex items-center justify-between">
          <span className="font-semibold uppercase tracking-wide">
            Structure comparison
          </span>
          <span className="font-mono text-[11px]">{result.uniprot_id}</span>
        </div>
        <div className="mt-1">{result.summary}</div>
      </div>

      {/* Overlap bar */}
      <div className="rounded-md border border-border bg-surface p-2">
        <div className="text-[11px] font-medium uppercase tracking-wide text-fg-subtle">
          Sequence coverage (AlphaFold: {af_len} residues)
        </div>
        <div className="mt-1 relative h-4 rounded bg-surface-2">
          {segments.map((seg, i) => (
            <div
              key={i}
              className={`absolute h-4 rounded ${seg.color}`}
              style={{ left: `${seg.left}%`, width: `${seg.width}%` }}
              title={seg.label}
            />
          ))}
        </div>
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-fg-muted">
          <div className="flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-sm bg-emerald-500" />
            Validated overlap: {overlap.overlap_residues} aa
          </div>
          <div className="flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-sm bg-purple-400" />
            Prediction-only: {overlap.predicted_only_residues} aa
          </div>
          {overlap.experimental_only_residues > 0 && (
            <div className="flex items-center gap-1">
              <span className="inline-block h-2 w-2 rounded-sm bg-rose-400" />
              Experimental-only: {overlap.experimental_only_residues} aa
            </div>
          )}
        </div>
      </div>

      {/* Comparison-level caveats */}
      {result.caveats.length > 0 && (
        <details
          open
          className="rounded border border-border bg-surface-2 px-2 py-1.5"
        >
          <summary className="cursor-pointer text-xs font-semibold text-warn">
            ⚠ Comparison caveats ({result.caveats.length})
          </summary>
          <ul className="ml-4 mt-1 list-disc space-y-1 text-[11px] text-warn">
            {result.caveats.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </details>
      )}

      {/* Tabs */}
      <div className="flex gap-1 border-b border-border text-xs">
        {(["side-by-side", "experimental", "predicted"] as Tab[]).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={`-mb-px border-b-2 px-3 py-1 ${
              tab === t
                ? "border-accent font-semibold text-fg"
                : "border-transparent text-fg-subtle hover:text-fg-muted"
            }`}
          >
            {t === "side-by-side"
              ? "Side by side"
              : t === "experimental"
                ? `Experimental (${result.experimental.pdb_id})`
                : `Predicted (${result.predicted.entry_id})`}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {tab === "side-by-side" && (
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          <PdbStructureCard structure={result.experimental} />
          <StructureCard structure={result.predicted} />
        </div>
      )}
      {tab === "experimental" && <PdbStructureCard structure={result.experimental} />}
      {tab === "predicted" && <StructureCard structure={result.predicted} />}
    </div>
  );
}
