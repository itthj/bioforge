// Pure adapter: off-target hits -> igv.js hg38 features (Slice B).
//
// Only hits the backend resolved to a GRCh38 chromosome (genomic_placement != null) can be
// drawn on hg38; everything else is returned separately so the UI can list it honestly
// rather than inventing a locus. No igv import here, so the placement/coordinate logic is
// trivially unit-testable.

import type { OfftargetHit, RiskLabel } from "../types/crispr";

/** igv.js hosted genome id used for the off-target view (no local 3 GB download). */
export const IGV_HG38_GENOME = "hg38";

/** Padding (bp) around the focused hit when computing the initial locus. */
const LOCUS_PADDING = 60;

export interface OfftargetIgvFeature {
  chr: string;
  start: number; // 0-based, inclusive (BED / igv convention)
  end: number; // 0-based, exclusive
  name: string;
  strand: "+" | "-";
  color: string;
}

const RISK_COLOR: Record<RiskLabel, string> = {
  high: "#e11d48", // rose-600
  medium: "#d97706", // amber-600
  low: "#64748b", // slate-500
};

const RISK_RANK: Record<RiskLabel, number> = { high: 3, medium: 2, low: 1 };

/** Split hits into those placeable on hg38 (have a genomic_placement) and the rest. */
export function splitByPlaceability(hits: OfftargetHit[]): {
  placeable: OfftargetHit[];
  nonPlaceable: OfftargetHit[];
} {
  const placeable: OfftargetHit[] = [];
  const nonPlaceable: OfftargetHit[] = [];
  for (const h of hits) {
    (h.genomic_placement ? placeable : nonPlaceable).push(h);
  }
  return { placeable, nonPlaceable };
}

/** Build igv annotation features for the placeable hits (placement coords pass through). */
export function buildOfftargetFeatures(hits: OfftargetHit[]): OfftargetIgvFeature[] {
  const features: OfftargetIgvFeature[] = [];
  for (const h of hits) {
    const p = h.genomic_placement;
    if (!p) continue;
    features.push({
      chr: p.chromosome,
      start: p.start,
      end: p.end,
      name: `${h.risk_label} risk (${h.mismatch_count}mm)`,
      strand: p.strand,
      color: RISK_COLOR[h.risk_label],
    });
  }
  return features;
}

/**
 * Initial igv locus: the highest-risk placeable hit's region, padded, as a 1-based
 * igv locus string. Returns null when nothing is placeable (caller shows table only).
 */
export function buildOfftargetLocus(hits: OfftargetHit[]): string | null {
  const placeable = hits.filter((h) => h.genomic_placement);
  if (placeable.length === 0) return null;
  const focus = placeable.reduce((best, h) =>
    RISK_RANK[h.risk_label] > RISK_RANK[best.risk_label] ? h : best,
  );
  const p = focus.genomic_placement!;
  // placement.start is 0-based; igv locus is 1-based inclusive.
  const start1 = Math.max(1, p.start + 1 - LOCUS_PADDING);
  const end1 = p.end + LOCUS_PADDING;
  return `${p.chromosome}:${start1}-${end1}`;
}

/** Full igv.createBrowser config for the hosted-hg38 off-target view. */
export function buildOfftargetIgvConfig(hits: OfftargetHit[]): {
  genome: string;
  locus: string | null;
  tracks: Array<Record<string, unknown>>;
} {
  return {
    genome: IGV_HG38_GENOME,
    locus: buildOfftargetLocus(hits),
    tracks: [
      {
        name: "Off-targets",
        type: "annotation",
        displayMode: "EXPANDED",
        height: 110,
        features: buildOfftargetFeatures(hits),
      },
    ],
  };
}
