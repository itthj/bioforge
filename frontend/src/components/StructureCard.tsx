import type { FetchAlphaFoldOutput } from "../types/structure";
import { MolstarViewer } from "./MolstarViewer";

interface StructureCardProps {
  structure: FetchAlphaFoldOutput;
}

const PLDDT_BIN_META: { key: keyof FetchAlphaFoldOutput["plddt_distribution"]; label: string; color: string }[] = [
  { key: "very_high", label: "Very high (≥90)", color: "bg-blue-600" },
  { key: "confident", label: "Confident (70-89)", color: "bg-cyan-500" },
  { key: "low", label: "Low (50-69)", color: "bg-amber-400" },
  { key: "very_low", label: "Very low (<50)", color: "bg-orange-500" },
];

/**
 * Renders an AlphaFold prediction:
 *   - Header: gene + organism + entry ID + UniProt accession
 *   - pLDDT confidence bar — stacked segments, one per confidence bin
 *   - Average pLDDT + length pill
 *   - Caveats (mandatory list — must always be visible, hence open <details>)
 *   - "View 3D" button that lazy-loads Mol* and renders the structure
 *   - Raw PDB text collapsible (in case the 3D viewer fails or for download)
 *
 * Mol* is loaded via dynamic import on first 3D-view click — keeps the initial
 * bundle small (Mol* is ~4 MB) and lets the page work even if the package
 * isn't installed yet (graceful fallback: show PDB text + install hint).
 */
export function StructureCard({ structure }: StructureCardProps) {
  const total = structure.length_residues || 1;
  return (
    <div className="space-y-3 rounded-md border border-slate-200 bg-slate-50 p-3">
      <div className="flex items-baseline justify-between">
        <div>
          <div className="text-sm font-semibold text-slate-800">
            {structure.gene ?? "Unknown gene"}
            <span className="ml-2 font-mono text-xs text-slate-500">
              {structure.uniprot_id}
            </span>
          </div>
          <div className="text-xs text-slate-600">
            {structure.organism ?? "Unknown organism"} ·{" "}
            <span className="font-mono">{structure.entry_id}</span>
            {structure.latest_version !== null && (
              <span className="ml-1 text-slate-400">v{structure.latest_version}</span>
            )}
          </div>
        </div>
        <div className="text-right">
          <div className="text-xs uppercase tracking-wide text-slate-500">
            avg pLDDT
          </div>
          <div className="font-mono text-lg font-semibold text-slate-800">
            {structure.average_plddt.toFixed(1)}
          </div>
          <div className="text-[11px] text-slate-500">
            {structure.length_residues} residues
          </div>
        </div>
      </div>

      {structure.uniprot_description && (
        <div className="text-xs italic text-slate-600">
          {structure.uniprot_description}
        </div>
      )}

      {/* pLDDT distribution bar */}
      <div>
        <div className="text-[11px] font-medium uppercase tracking-wide text-slate-500">
          pLDDT confidence distribution
        </div>
        <div
          className="mt-1 flex h-3 overflow-hidden rounded"
          role="img"
          aria-label="pLDDT confidence distribution"
        >
          {PLDDT_BIN_META.map((bin) => {
            const count = structure.plddt_distribution[bin.key];
            const pct = (count / total) * 100;
            if (pct === 0) return null;
            return (
              <div
                key={bin.key}
                className={bin.color}
                style={{ width: `${pct}%` }}
                title={`${bin.label}: ${count} residues (${pct.toFixed(1)}%)`}
              />
            );
          })}
        </div>
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-slate-600">
          {PLDDT_BIN_META.map((bin) => {
            const count = structure.plddt_distribution[bin.key];
            return (
              <div key={bin.key} className="flex items-center gap-1">
                <span className={`inline-block h-2 w-2 rounded-sm ${bin.color}`} />
                <span>
                  {bin.label}: <span className="font-mono">{count}</span>
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Caveats — open by default. These are non-negotiable context. */}
      <details open className="rounded border border-amber-200 bg-amber-50 px-2 py-1.5">
        <summary className="cursor-pointer text-xs font-semibold text-amber-900">
          ⚠ Prediction caveats ({structure.caveats.length})
        </summary>
        <ul className="ml-4 mt-1 list-disc space-y-1 text-[11px] text-amber-900">
          {structure.caveats.map((c, i) => (
            <li key={i}>{c}</li>
          ))}
        </ul>
      </details>

      {/* 3D viewer (lazy-loaded) */}
      <MolstarViewer pdbText={structure.pdb_text} pdbUrl={structure.pdb_url} />

      {/* Raw PDB text collapsible */}
      {structure.pdb_text && (
        <details className="text-xs">
          <summary className="cursor-pointer text-slate-500 hover:text-slate-700">
            Raw PDB text ({(structure.pdb_text.length / 1024).toFixed(1)} KB)
          </summary>
          <pre className="mt-1 max-h-60 overflow-auto rounded bg-white p-2 font-mono text-[10px] text-slate-700">
            {structure.pdb_text.slice(0, 8000)}
            {structure.pdb_text.length > 8000 && "\n…[truncated]"}
          </pre>
        </details>
      )}
    </div>
  );
}

