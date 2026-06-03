import type { AlignedSequence } from "../types/msa";

// Lightweight, dependency-free MSA renderer.
//
// We deliberately do NOT use react-msa-viewer: its peer deps are React >=15 <17, incompatible
// with this project's React 18 (verified 2026-06-02), and the maintained alternatives drag in
// JBrowse + MUI + mobx, against the lean-stack principle (§3). This renders the REAL align_msa
// output (colored residue grid + per-column conservation) as inline elements, exactly the way
// ReliabilityDiagram hand-rolls inline SVG instead of pulling a chart library.

const NUC_COLORS: Record<string, string> = {
  A: "#b7e4c7", // green
  C: "#a9d6e5", // blue
  G: "#ffe5a0", // amber
  T: "#f6c9c9", // red
  U: "#f6c9c9",
  N: "#e2e8f0",
};

// Coarse amino-acid grouping by physicochemical property (Clustal-ish), for protein MSAs.
const AA_GROUP_COLOR: Record<string, string> = {
  // hydrophobic
  A: "#cfe8ff", I: "#cfe8ff", L: "#cfe8ff", M: "#cfe8ff", F: "#cfe8ff", W: "#cfe8ff", V: "#cfe8ff",
  // positive
  K: "#ffd6d6", R: "#ffd6d6", H: "#ffd6d6",
  // negative
  D: "#ffe0b3", E: "#ffe0b3",
  // polar
  S: "#d5f5e3", T: "#d5f5e3", N: "#d5f5e3", Q: "#d5f5e3", C: "#d5f5e3", Y: "#d5f5e3",
  // special
  G: "#f0f0f0", P: "#f0f0f0",
};

const GAP_COLOR = "#ffffff";

function isNucleotideAlignment(rows: AlignedSequence[]): boolean {
  const seen = new Set<string>();
  for (const r of rows) for (const ch of r.aligned_sequence.toUpperCase()) if (ch !== "-") seen.add(ch);
  for (const ch of seen) if (!"ACGTUN".includes(ch)) return false;
  return seen.size > 0;
}

function residueColor(ch: string, nucleotide: boolean): string {
  if (ch === "-") return GAP_COLOR;
  const u = ch.toUpperCase();
  return (nucleotide ? NUC_COLORS[u] : AA_GROUP_COLOR[u]) ?? "#eef2f7";
}

/**
 * Per-column conservation: fraction of rows sharing the most common symbol in that column.
 * Deterministic, derived from the alignment (never fabricated). A column whose most common
 * symbol covers every row AND is not a gap is "fully conserved" (marked '*', Clustal-style).
 */
export function columnConservation(
  rows: AlignedSequence[],
  alignmentLength: number,
): { fraction: number; fullyConserved: boolean }[] {
  const n = rows.length;
  const out: { fraction: number; fullyConserved: boolean }[] = [];
  for (let col = 0; col < alignmentLength; col++) {
    const counts = new Map<string, number>();
    for (const r of rows) {
      const ch = r.aligned_sequence[col] ?? "-";
      counts.set(ch, (counts.get(ch) ?? 0) + 1);
    }
    let topChar = "-";
    let top = 0;
    for (const [ch, c] of counts) {
      if (c > top) {
        top = c;
        topChar = ch;
      }
    }
    out.push({ fraction: n ? top / n : 0, fullyConserved: top === n && topChar !== "-" && n > 1 });
  }
  return out;
}

interface MsaViewerProps {
  aligned: AlignedSequence[];
  alignmentLength: number;
}

export function MsaViewer({ aligned, alignmentLength }: MsaViewerProps) {
  if (aligned.length === 0) {
    return <div className="text-xs text-fg-subtle">No aligned sequences to display.</div>;
  }
  const nucleotide = isNucleotideAlignment(aligned);
  const conservation = columnConservation(aligned, alignmentLength);
  const labelWidth = Math.min(
    16,
    Math.max(4, ...aligned.map((r) => r.id.length)),
  );

  // Position ruler: a tick label every 10 columns.
  const ruler: string[] = [];
  for (let i = 0; i < alignmentLength; i++) {
    ruler.push((i + 1) % 10 === 0 ? String(i + 1) : "");
  }

  return (
    <div className="overflow-x-auto rounded border border-border bg-white p-2">
      <div className="inline-block font-mono text-[11px] leading-tight">
        {/* ruler */}
        <div className="flex whitespace-pre text-[9px] text-slate-400">
          <span style={{ width: `${labelWidth}ch` }} className="inline-block shrink-0" />
          {ruler.map((tick, i) => (
            <span key={i} className="inline-block w-[1ch] text-center">
              {tick ? "|" : " "}
            </span>
          ))}
        </div>

        {/* sequence rows */}
        {aligned.map((row) => (
          <div key={row.id} className="flex whitespace-pre">
            <span
              style={{ width: `${labelWidth}ch` }}
              className="inline-block shrink-0 truncate pr-1 text-slate-700"
              title={row.id}
            >
              {row.id}
            </span>
            {Array.from({ length: alignmentLength }, (_, col) => {
              const ch = row.aligned_sequence[col] ?? "-";
              return (
                <span
                  key={col}
                  className="inline-block w-[1ch] text-center text-slate-900"
                  style={{ backgroundColor: residueColor(ch, nucleotide) }}
                >
                  {ch}
                </span>
              );
            })}
          </div>
        ))}

        {/* conservation row: '*' fully conserved, '.' >=50%, ' ' otherwise */}
        <div className="flex whitespace-pre text-emerald-700" aria-label="conservation">
          <span style={{ width: `${labelWidth}ch` }} className="inline-block shrink-0" />
          {conservation.map((c, i) => (
            <span key={i} className="inline-block w-[1ch] text-center">
              {c.fullyConserved ? "*" : c.fraction >= 0.5 ? "." : " "}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
