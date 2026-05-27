import type { FetchInterproOutput, InterproDomain } from "../types/interpro";

interface InterproCardProps {
  output: FetchInterproOutput;
  /**
   * Optional protein length — if provided, renders a horizontal sequence bar
   * with each domain plotted at its actual residue range. If omitted, just
   * lists the domains as text.
   */
  proteinLength?: number;
}

const TYPE_COLORS: Record<string, string> = {
  domain: "bg-emerald-500",
  family: "bg-indigo-500",
  homologous_superfamily: "bg-violet-500",
  repeat: "bg-amber-500",
  active_site: "bg-rose-500",
  binding_site: "bg-pink-500",
  conserved_site: "bg-cyan-500",
  ptm: "bg-fuchsia-500",
};

const TYPE_LABELS: Record<string, string> = {
  domain: "Domain",
  family: "Family",
  homologous_superfamily: "Superfamily",
  repeat: "Repeat",
  active_site: "Active site",
  binding_site: "Binding site",
  conserved_site: "Conserved site",
  ptm: "PTM",
};

function colorFor(type: string): string {
  return TYPE_COLORS[type] ?? "bg-slate-400";
}

/**
 * Renders InterPro domain annotations.
 *
 * If a proteinLength is provided, each domain gets a horizontal track with
 * colored bars at its residue ranges — a "ProtVista-lite" view that mirrors
 * what the frontend overlays on the structure cards.
 */
export function InterproCard({ output, proteinLength }: InterproCardProps) {
  const totalLen = proteinLength && proteinLength > 0 ? proteinLength : null;

  return (
    <div className="space-y-3 rounded-md border border-slate-200 bg-slate-50 p-3">
      <div className="flex items-baseline justify-between">
        <div className="text-sm font-semibold text-slate-800">
          InterPro annotations
          <span className="ml-2 font-mono text-xs text-slate-500">
            {output.uniprot_id}
          </span>
        </div>
        <div className="text-xs text-slate-600">
          {output.num_entries} entries
        </div>
      </div>

      {/* Type legend */}
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-700">
        {Array.from(new Set(output.domains.map((d) => d.type))).map((t) => (
          <div key={t} className="flex items-center gap-1">
            <span className={`inline-block h-2 w-2 rounded-sm ${colorFor(t)}`} />
            <span>{TYPE_LABELS[t] ?? t}</span>
          </div>
        ))}
      </div>

      {/* Domain list — one row per InterPro entry. */}
      <ul className="space-y-1.5">
        {output.domains.map((d) => (
          <DomainRow key={d.interpro_id} domain={d} totalLength={totalLen} />
        ))}
      </ul>

      {/* Caveats */}
      {output.caveats.length > 0 && (
        <details className="rounded border border-amber-200 bg-amber-50 px-2 py-1.5">
          <summary className="cursor-pointer text-xs font-semibold text-amber-900">
            ⚠ Caveats ({output.caveats.length})
          </summary>
          <ul className="ml-4 mt-1 list-disc space-y-1 text-[11px] text-amber-900">
            {output.caveats.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

interface DomainRowProps {
  domain: InterproDomain;
  totalLength: number | null;
}

function DomainRow({ domain, totalLength }: DomainRowProps) {
  return (
    <li className="rounded border border-slate-200 bg-white px-2 py-1">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-xs">
            <a
              href={`https://www.ebi.ac.uk/interpro/entry/InterPro/${domain.interpro_id}/`}
              target="_blank"
              rel="noopener noreferrer"
              className="font-mono font-semibold text-blue-700 hover:underline"
            >
              {domain.interpro_id}
            </a>
            <span className="ml-2 text-slate-800">{domain.name}</span>
          </div>
          <div className="mt-0.5 text-[11px] text-slate-500">
            {TYPE_LABELS[domain.type] ?? domain.type} ·{" "}
            {domain.regions.length} region
            {domain.regions.length === 1 ? "" : "s"} ·{" "}
            {domain.regions
              .map((r) => `${r.start}-${r.end}`)
              .join(", ")}
          </div>
        </div>
      </div>
      {totalLength && (
        <div
          className="mt-1 relative h-2 rounded bg-slate-100"
          role="img"
          aria-label={`Domain ${domain.name} positions`}
        >
          {domain.regions.map((r, i) => {
            const left = ((r.start - 1) / totalLength) * 100;
            const width = Math.max(
              0.5,
              ((r.end - r.start + 1) / totalLength) * 100,
            );
            return (
              <div
                key={i}
                className={`absolute h-2 rounded ${colorFor(domain.type)}`}
                style={{ left: `${left}%`, width: `${width}%` }}
                title={`${domain.name}: ${r.start}-${r.end}`}
              />
            );
          })}
        </div>
      )}
    </li>
  );
}
