import { useEffect, useRef, useState } from "react";

import type { FetchAlphaFoldOutput } from "../types/structure";

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
      <Viewer3D pdbText={structure.pdb_text} pdbUrl={structure.pdb_url} />

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

// --- 3D viewer: lazy-loaded Mol* with graceful fallback ----------------------

type ViewerState = "idle" | "loading" | "ready" | "error" | "missing-pdb";

interface Viewer3DProps {
  pdbText: string | null;
  pdbUrl: string;
}

function Viewer3D({ pdbText, pdbUrl }: Viewer3DProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const pluginRef = useRef<unknown>(null);
  const [state, setState] = useState<ViewerState>(
    pdbText ? "idle" : "missing-pdb",
  );
  const [errorMsg, setErrorMsg] = useState<string>("");

  useEffect(() => {
    return () => {
      // Best-effort cleanup of any plugin instance we created.
      const plugin = pluginRef.current as { dispose?: () => void } | null;
      if (plugin && typeof plugin.dispose === "function") {
        try {
          plugin.dispose();
        } catch {
          // Swallow — disposal failures during unmount shouldn't blow up React.
        }
      }
      pluginRef.current = null;
    };
  }, []);

  async function handleLoad() {
    if (!pdbText || !containerRef.current) return;
    setState("loading");
    setErrorMsg("");
    try {
      // Dynamic import keeps Mol* (~4 MB) out of the initial bundle and lets the
      // app run without Mol* installed (the catch below renders an install hint).
      //
      // We import a small, stable Mol* surface: `createPluginUI` from the
      // mol-plugin-ui entry. If Mol* upgrades break this import, the catch
      // will tell the user exactly what failed.
      const mod = (await import(
        /* @vite-ignore */ "molstar/lib/mol-plugin-ui"
      )) as { createPluginUI: (opts: unknown) => Promise<unknown> };
      const specMod = (await import(
        /* @vite-ignore */ "molstar/lib/mol-plugin-ui/spec"
      )) as { DefaultPluginUISpec: () => unknown };
      const reactMod = (await import(
        /* @vite-ignore */ "molstar/lib/mol-plugin-ui/react18"
      )) as { renderReact18: unknown };

      const plugin = (await mod.createPluginUI({
        target: containerRef.current,
        spec: specMod.DefaultPluginUISpec(),
        render: reactMod.renderReact18,
      })) as {
        loadStructureFromData: (data: string, format: string) => Promise<unknown>;
        dispose?: () => void;
      };
      pluginRef.current = plugin;
      await plugin.loadStructureFromData(pdbText, "pdb");
      setState("ready");
    } catch (err) {
      const msg =
        err && typeof err === "object" && "message" in err
          ? String((err as { message: unknown }).message)
          : String(err);
      // Distinguish "package not installed" from "render failed at runtime".
      const looksLikeMissingPackage =
        msg.includes("Failed to resolve module") ||
        msg.includes("Cannot find module") ||
        msg.includes("MODULE_NOT_FOUND") ||
        msg.toLowerCase().includes("molstar");
      setErrorMsg(
        looksLikeMissingPackage
          ? `Mol* viewer not installed. Run \`npm install molstar\` in the frontend/ directory to enable 3D rendering. PDB text is available below for download.`
          : `Mol* failed to render: ${msg}`,
      );
      setState("error");
    }
  }

  if (state === "missing-pdb") {
    return (
      <div className="rounded border border-slate-200 bg-white p-2 text-xs text-slate-600">
        No PDB text in this response. Download directly from{" "}
        <a
          href={pdbUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="text-blue-700 underline"
        >
          {pdbUrl}
        </a>
        .
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-medium uppercase tracking-wide text-slate-500">
          3D viewer
        </span>
        {state === "idle" && (
          <button
            type="button"
            onClick={handleLoad}
            className="rounded bg-slate-800 px-2 py-0.5 text-[11px] font-medium text-white hover:bg-slate-900"
          >
            Load Mol* viewer
          </button>
        )}
        {state === "loading" && (
          <span className="text-[11px] italic text-slate-500">Loading Mol*…</span>
        )}
        {state === "ready" && (
          <span className="text-[11px] italic text-emerald-700">Loaded</span>
        )}
        {state === "error" && (
          <button
            type="button"
            onClick={handleLoad}
            className="text-[11px] text-rose-700 underline"
          >
            Retry
          </button>
        )}
      </div>
      {state === "error" && (
        <div className="mt-1 rounded border border-rose-200 bg-rose-50 px-2 py-1 text-[11px] text-rose-800">
          {errorMsg}
        </div>
      )}
      <div
        ref={containerRef}
        className={`mt-1 rounded border border-slate-200 bg-black ${
          state === "ready" ? "h-72" : "h-0"
        }`}
        // Mol* writes its canvas into this div. Height stays 0 until load to avoid a black box.
      />
    </div>
  );
}
