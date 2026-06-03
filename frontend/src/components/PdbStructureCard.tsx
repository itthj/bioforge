import type { FetchPdbOutput } from "../types/pdb_structure";
import { MolstarViewer } from "./MolstarViewer";

interface PdbStructureCardProps {
  structure: FetchPdbOutput;
}

/**
 * Renders an experimental structure from the RCSB PDB.
 *
 * Different from StructureCard (AlphaFold prediction):
 *   - No pLDDT — experimental structures don't have it.
 *   - Has resolution + experimental method + deposit/release dates.
 *   - Shows chains as pills with residue counts.
 *   - Lists ligand chemical IDs (cofactors, drugs, metals, modified residues).
 *   - Caveats are method-specific (X-ray crystal contacts, cryo-EM local
 *     resolution, NMR ensemble interpretation) — built on the backend.
 *
 * The Mol* viewer is shared via MolstarViewer (lazy-loaded on click).
 */
export function PdbStructureCard({ structure }: PdbStructureCardProps) {
  return (
    <div className="space-y-3 rounded-md border border-border bg-bg p-3">
      {/* Header */}
      <div className="flex items-baseline justify-between">
        <div>
          <div className="text-sm font-semibold text-fg">
            <a
              href={`https://www.rcsb.org/structure/${structure.pdb_id}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent hover:underline"
            >
              {structure.pdb_id}
            </a>
            {structure.title && (
              <span className="ml-2 font-normal text-fg-muted">
                {structure.title}
              </span>
            )}
          </div>
          <div className="text-xs text-fg-muted">
            {structure.experimental_method ?? "Unknown method"}
            {structure.resolution_angstrom !== null && (
              <span className="ml-2 font-mono">
                {structure.resolution_angstrom.toFixed(2)} Å
              </span>
            )}
            {structure.release_date && (
              <span className="ml-2 text-fg-subtle">
                released {structure.release_date}
              </span>
            )}
          </div>
        </div>
        <div className="text-right text-xs">
          <div>
            <span className="font-mono font-semibold text-fg">
              {structure.num_chains}
            </span>{" "}
            <span className="text-fg-subtle">chains</span>
          </div>
          <div>
            <span className="font-mono font-semibold text-fg">
              {structure.num_residues}
            </span>{" "}
            <span className="text-fg-subtle">residues</span>
          </div>
          {structure.mean_b_factor !== null && (
            <div className="text-[11px] text-fg-subtle">
              ⟨B⟩ {structure.mean_b_factor.toFixed(1)} Å²
            </div>
          )}
        </div>
      </div>

      {/* Keywords */}
      {structure.keywords && (
        <div className="text-[11px] italic text-fg-muted">
          {structure.keywords}
        </div>
      )}

      {/* Chain pills */}
      {structure.chain_ids.length > 0 && (
        <div>
          <div className="text-[11px] font-medium uppercase tracking-wide text-fg-subtle">
            Chains
          </div>
          <div className="mt-1 flex flex-wrap gap-1">
            {structure.chain_ids.map((chain) => (
              <span
                key={chain}
                className="inline-flex items-center gap-1 rounded-full border border-border bg-surface px-2 py-0.5 text-[11px] font-mono"
              >
                <span className="font-semibold text-fg">{chain}</span>
                <span className="text-fg-subtle">
                  {structure.residues_per_chain[chain] ?? 0}aa
                </span>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Ligand pills */}
      {structure.ligand_ids.length > 0 && (
        <div>
          <div className="text-[11px] font-medium uppercase tracking-wide text-fg-subtle">
            Ligands / cofactors ({structure.ligand_ids.length})
          </div>
          <div className="mt-1 flex flex-wrap gap-1">
            {structure.ligand_ids.map((lig) => (
              <a
                key={lig}
                href={`https://www.rcsb.org/ligand/${lig}`}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-block rounded border border-border bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-warn hover:bg-surface-2"
                title={`Look up ${lig} in RCSB Ligand Catalog`}
              >
                {lig}
              </a>
            ))}
          </div>
        </div>
      )}

      {/* Caveats — open by default. */}
      <details
        open
        className="rounded border border-border bg-surface-2 px-2 py-1.5"
      >
        <summary className="cursor-pointer text-xs font-semibold text-warn">
          ⚠ Interpretation caveats ({structure.caveats.length})
        </summary>
        <ul className="ml-4 mt-1 list-disc space-y-1 text-[11px] text-warn">
          {structure.caveats.map((c, i) => (
            <li key={i}>{c}</li>
          ))}
        </ul>
      </details>

      {/* 3D viewer (lazy-loaded). When the entry is mmCIF-only (very large
          complexes — ribosomes, capsids), backend returns cif_text + format='cif'
          and Mol* uses its mmCIF loader. */}
      <MolstarViewer
        structureText={
          structure.structure_format === "cif"
            ? (structure.cif_text ?? null)
            : structure.pdb_text
        }
        format={structure.structure_format ?? "pdb"}
        downloadUrl={
          structure.structure_format === "cif"
            ? structure.cif_url
            : structure.pdb_url
        }
      />

      {/* Raw structure text collapsible */}
      {(() => {
        const fmt = structure.structure_format ?? "pdb";
        const rawText = fmt === "cif" ? structure.cif_text : structure.pdb_text;
        if (!rawText) return null;
        return (
          <details className="text-xs">
            <summary className="cursor-pointer text-fg-subtle hover:text-fg-muted">
              Raw {fmt.toUpperCase()} text ({(rawText.length / 1024).toFixed(1)} KB)
            </summary>
            <pre className="mt-1 max-h-60 overflow-auto rounded bg-surface p-2 font-mono text-[10px] text-fg-muted">
              {rawText.slice(0, 8000)}
              {rawText.length > 8000 && "\n…[truncated]"}
            </pre>
          </details>
        );
      })()}
    </div>
  );
}
