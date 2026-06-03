import type { FindBestStructureOutput } from "../types/find_best_structure";
import { PdbStructureCard } from "./PdbStructureCard";
import { StructureCard } from "./StructureCard";

interface FindBestStructureCardProps {
  result: FindBestStructureOutput;
}

/**
 * Renders the composite find_best_structure output:
 *   - A short banner showing which source was chosen and why
 *   - The full child card (PdbStructureCard or StructureCard) embedded below
 *   - Any composite-level caveats (low coverage, fallback reason, etc.)
 *   - The alternative experimental candidates that were considered, even when
 *     a prediction was returned — gives the agent / user the full decision audit
 */
export function FindBestStructureCard({ result }: FindBestStructureCardProps) {
  const isExperimental = result.source === "experimental";
  return (
    <div className="space-y-3">
      {/* Decision banner */}
      <div
        className={`rounded-md border p-2 text-xs ${
          isExperimental
            ? "border-border bg-surface-2 text-accent"
            : "border-border bg-surface-2 text-accent"
        }`}
      >
        <div className="flex items-center justify-between">
          <span className="font-semibold uppercase tracking-wide">
            {isExperimental ? "Experimental structure chosen" : "Predicted structure chosen"}
          </span>
          <span className="font-mono text-[11px]">{result.uniprot_id}</span>
        </div>
        <div className="mt-1">{result.reason}</div>
      </div>

      {/* Composite-level caveats */}
      {result.caveats.length > 0 && (
        <details
          open
          className="rounded border border-border bg-surface-2 px-2 py-1.5"
        >
          <summary className="cursor-pointer text-xs font-semibold text-warn">
            ⚠ Decision caveats ({result.caveats.length})
          </summary>
          <ul className="ml-4 mt-1 list-disc space-y-1 text-[11px] text-warn">
            {result.caveats.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </details>
      )}

      {/* Alternative candidates table — only when multiple were considered or
          when a prediction was returned despite candidates existing. */}
      {result.experimental_candidates.length > 1 && (
        <details className="rounded border border-border bg-surface px-2 py-1.5 text-xs">
          <summary className="cursor-pointer font-semibold text-fg-muted">
            Alternative experimental candidates ({result.experimental_candidates.length})
          </summary>
          <table className="mt-1 w-full table-fixed border-collapse text-[11px]">
            <thead>
              <tr className="text-left text-fg-subtle">
                <th className="px-1 py-0.5">PDB</th>
                <th className="px-1 py-0.5">Chain</th>
                <th className="px-1 py-0.5">Coverage</th>
                <th className="px-1 py-0.5">Resolution</th>
                <th className="px-1 py-0.5">Method</th>
              </tr>
            </thead>
            <tbody>
              {result.experimental_candidates.map((c) => (
                <tr key={`${c.pdb_id}-${c.chain_id ?? ""}`} className="border-t border-border">
                  <td className="px-1 py-0.5 font-mono">
                    <a
                      href={`https://www.rcsb.org/structure/${c.pdb_id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-accent hover:underline"
                    >
                      {c.pdb_id}
                    </a>
                  </td>
                  <td className="px-1 py-0.5 font-mono">{c.chain_id ?? "?"}</td>
                  <td className="px-1 py-0.5">
                    {c.coverage !== null ? `${(c.coverage * 100).toFixed(0)}%` : "?"}
                  </td>
                  <td className="px-1 py-0.5">
                    {c.resolution_angstrom !== null ? `${c.resolution_angstrom.toFixed(2)} Å` : "?"}
                  </td>
                  <td className="px-1 py-0.5 text-fg-muted">{c.experimental_method ?? "?"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}

      {/* Embedded child card */}
      {isExperimental && result.pdb_result && (
        <PdbStructureCard structure={result.pdb_result} />
      )}
      {!isExperimental && result.alphafold_result && (
        <StructureCard structure={result.alphafold_result} />
      )}
      {/* Defensive fallback: result claims a source but the embedded child is
          missing. Shouldn't happen if the backend contract holds, but worth a
          visible error rather than a silent blank panel. */}
      {isExperimental && !result.pdb_result && (
        <div className="rounded border border-border bg-surface-2 p-2 text-xs text-danger">
          Backend returned source='experimental' but no pdb_result. This is a contract bug.
        </div>
      )}
      {!isExperimental && !result.alphafold_result && (
        <div className="rounded border border-border bg-surface-2 p-2 text-xs text-danger">
          Backend returned source='predicted' but no alphafold_result. This is a contract bug.
        </div>
      )}
    </div>
  );
}
