import type { AlignMsaOutput } from "../types/msa";
import { MsaViewer } from "./MsaViewer";

interface MsaCardProps {
  output: AlignMsaOutput;
}

export function MsaCard({ output }: MsaCardProps) {
  return (
    <div className="space-y-2 rounded-md border border-border bg-surface p-3 shadow-sm">
      <header>
        <div className="text-xs font-semibold uppercase tracking-wider text-accent">
          Multiple-sequence alignment
        </div>
        <div className="font-mono text-xs text-fg-subtle">
          {output.num_sequences} sequences · {output.alignment_length} columns ·{" "}
          {output.method}
        </div>
      </header>

      <MsaViewer aligned={output.aligned} alignmentLength={output.alignment_length} />

      <div className="text-[10px] text-fg-subtle">
        <span className="font-medium">*</span> fully conserved column ·{" "}
        <span className="font-medium">.</span> ≥50% agreement. Conservation is computed
        from this alignment.
      </div>

      {output.notes.length > 0 && (
        <ul className="ml-4 list-disc text-[11px] text-fg-muted">
          {output.notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
