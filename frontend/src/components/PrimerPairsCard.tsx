import type { DesignPrimersOutput, PrimerPair } from "../types/primers";
import { downloadBlob, toCsv } from "../lib/download";
import { ExportButton } from "./ui/ExportButton";

interface PrimerPairsCardProps {
  output: DesignPrimersOutput;
}

/** One CSV row per primer pair, both strands flattened — the shape an ordering sheet wants. */
export function primersToCsv(output: DesignPrimersOutput): string {
  const header = [
    "pair",
    "product_size",
    "pair_penalty",
    "forward_sequence",
    "forward_tm",
    "forward_gc_percent",
    "forward_start",
    "forward_length",
    "reverse_sequence",
    "reverse_tm",
    "reverse_gc_percent",
    "reverse_start",
    "reverse_length",
  ];
  const rows = output.primer_pairs.map((p) => [
    p.rank + 1,
    p.product_size,
    p.pair_penalty,
    p.forward_sequence,
    p.forward_tm,
    p.forward_gc_percent,
    p.forward_start,
    p.forward_length,
    p.reverse_sequence,
    p.reverse_tm,
    p.reverse_gc_percent,
    p.reverse_start,
    p.reverse_length,
  ]);
  return toCsv([header, ...rows]);
}

export function PrimerPairsCard({ output }: PrimerPairsCardProps) {
  return (
    <div className="space-y-3 rounded-md border border-border bg-surface p-3 shadow-sm">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-accent">
            PCR primer pairs (primer3)
          </div>
          <div className="font-mono text-xs text-fg-subtle">
            template {output.template_length} nt
            {output.target_start !== null && output.target_end !== null
              ? ` · target ${output.target_start}-${output.target_end}`
              : ""}
            {" · "}
            {output.num_returned} pair{output.num_returned === 1 ? "" : "s"}
          </div>
        </div>
        {output.num_returned > 0 && (
          <ExportButton
            label="Export CSV"
            title="Download the primer pairs as CSV"
            onClick={() =>
              downloadBlob("primer_pairs.csv", "text/csv;charset=utf-8", primersToCsv(output))
            }
          />
        )}
      </header>

      {output.num_returned === 0 ? (
        <div className="rounded-md border border-dashed border-border bg-surface-2 p-3 text-xs text-warn">
          <div className="font-semibold">No primer pairs found</div>
          {output.primer3_warnings.length > 0 && (
            <ul className="mt-1 ml-4 list-disc space-y-1">
              {output.primer3_warnings.map((w, i) => (
                <li key={i} className="font-mono text-[11px]">
                  {w}
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : (
        <ol className="space-y-2">
          {output.primer_pairs.map((pair) => (
            <li key={pair.rank}>
              <PrimerPairRow pair={pair} />
            </li>
          ))}
        </ol>
      )}

      {output.caveats.length > 0 && (
        <div className="rounded border border-border bg-surface-2 p-2 text-[11px] text-warn">
          <div className="mb-1 font-semibold">Caveats</div>
          <ul className="ml-4 list-disc space-y-1">
            {output.caveats.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function PrimerPairRow({ pair }: { pair: PrimerPair }) {
  return (
    <div className="rounded-md border border-border bg-surface p-2 shadow-sm">
      <div className="mb-2 flex items-center gap-2 text-xs">
        <span className="font-semibold text-fg-muted">Pair #{pair.rank + 1}</span>
        <span className="font-mono text-[11px] text-fg-subtle">
          {pair.product_size} bp · penalty {pair.pair_penalty.toFixed(3)}
        </span>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <PrimerStrand
          label="Forward"
          sequence={pair.forward_sequence}
          tm={pair.forward_tm}
          gc={pair.forward_gc_percent}
          start={pair.forward_start}
          length={pair.forward_length}
        />
        <PrimerStrand
          label="Reverse"
          sequence={pair.reverse_sequence}
          tm={pair.reverse_tm}
          gc={pair.reverse_gc_percent}
          start={pair.reverse_start - pair.reverse_length + 1}
          length={pair.reverse_length}
        />
      </div>
    </div>
  );
}

function PrimerStrand({
  label,
  sequence,
  tm,
  gc,
  start,
  length,
}: {
  label: string;
  sequence: string;
  tm: number;
  gc: number;
  start: number;
  length: number;
}) {
  return (
    <div className="rounded bg-bg px-2 py-1.5">
      <div className="flex items-baseline justify-between">
        <span className="text-[10px] font-medium uppercase tracking-wider text-fg-subtle">
          {label}
        </span>
        <span className="font-mono text-[10px] text-fg-subtle">
          {length} nt @ {start}
        </span>
      </div>
      <div className="mt-1 break-all font-mono text-xs text-fg">{sequence}</div>
      <div className="mt-1 flex gap-3 font-mono text-[11px] text-fg-muted">
        <span>Tm {tm.toFixed(1)}°C</span>
        <span>GC {gc.toFixed(1)}%</span>
      </div>
    </div>
  );
}
