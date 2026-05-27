import { useEffect, useRef, useState } from "react";

interface MolstarViewerProps {
  /** Full PDB text to render. If null, the viewer shows a download-link fallback. */
  pdbText: string | null;
  /** Source URL — shown in the missing-PDB fallback so the user can grab the file directly. */
  pdbUrl: string;
}

type ViewerState = "idle" | "loading" | "ready" | "error" | "missing-pdb";

/**
 * Lazy-loaded Mol* (`molstar`) viewer.
 *
 * Mol* is ~4 MB. We dynamically `import()` it on the first "Load" click, so the
 * initial app bundle stays small and the app still works if molstar isn't
 * installed (the catch path renders an install hint).
 *
 * Shared by StructureCard (AlphaFold predictions) and PdbStructureCard (RCSB
 * experimental structures) — the renderer doesn't care what produced the PDB.
 */
export function MolstarViewer({ pdbText, pdbUrl }: MolstarViewerProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const pluginRef = useRef<unknown>(null);
  const [state, setState] = useState<ViewerState>(
    pdbText ? "idle" : "missing-pdb",
  );
  const [errorMsg, setErrorMsg] = useState<string>("");

  useEffect(() => {
    return () => {
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
      />
    </div>
  );
}
