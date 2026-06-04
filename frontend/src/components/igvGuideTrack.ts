// Pure adapter: CRISPR edit report -> igv.js reference + feature track.
//
// Kept free of any igv.js import so it is trivially unit-testable and carries the
// coordinate logic the genome-browser view depends on. The React component
// (IgvGuideViewer.tsx) consumes these outputs and feeds them to igv.createBrowser.
//
// Coordinate contract (locked by backend test_crispr_edit_report.py): every guide's
// protospacer_start/end + pam_start/end are 0-based half-open offsets on the FORWARD
// strand of target_sequence. That is exactly igv.js's feature convention (BED-like:
// 0-based start, exclusive end), so the fields pass straight through with no math.

import type {
  CrisprEditReportOutput,
  GuideReport,
  RecommendationLabel,
} from "../types/crispr";

/** Single-contig name for the submitted-sequence "genome". Features reference this. */
export const TARGET_CONTIG = "target";

/** An igv.js annotation feature. `kind` is for our tests/clarity; igv ignores it. */
export interface IgvGuideFeature {
  chr: string;
  /** 0-based, inclusive. */
  start: number;
  /** 0-based, exclusive (BED convention) — matches design_guides coordinates. */
  end: number;
  name: string;
  strand: "+" | "-";
  color: string;
  kind: "protospacer" | "pam" | "cut";
}

// Protospacer color follows the report's recommendation label (mirrors the card's
// LABEL_STYLES palette, as hex so igv can render it). PAM + cut use fixed colors.
const LABEL_COLOR: Record<RecommendationLabel, string> = {
  preferred: "#059669", // emerald-600
  acceptable: "#0284c7", // sky-600
  caution: "#d97706", // amber-600
  avoid: "#e11d48", // rose-600
};
const PAM_COLOR = "#475569"; // slate-600
const CUT_COLOR = "#dc2626"; // red-600

/**
 * Build a single-contig FASTA for the submitted target so igv.js can render it as
 * the reference sequence. Wrapped at 60 columns (the conventional width); igv reads
 * it identically wrapped or not.
 */
export function buildTargetFasta(sequence: string, contig: string = TARGET_CONTIG): string {
  const wrapped = sequence.match(/.{1,60}/g)?.join("\n") ?? "";
  return `>${contig}\n${wrapped}\n`;
}

/**
 * Convert each candidate guide into igv annotation features: the protospacer, its
 * PAM, and (only when the backend simulated edit outcomes) the cut site.
 *
 * The cut marker reuses the backend's authoritative `cut_position_fwd` rather than
 * recomputing a cut definition here — there must be exactly one source of truth for
 * where Cas9 cuts. When a guide was not simulated (no edit_outcome_summary), no cut
 * feature is emitted (we never guess the position).
 */
export function buildGuideFeatures(
  report: CrisprEditReportOutput,
  contig: string = TARGET_CONTIG,
): IgvGuideFeature[] {
  const features: IgvGuideFeature[] = [];
  for (const g of report.guides) {
    const color = LABEL_COLOR[g.recommendation_label] ?? PAM_COLOR;
    const tag = `g${g.rank}`;
    features.push({
      chr: contig,
      start: g.protospacer_start,
      end: g.protospacer_end,
      name: `${tag} protospacer`,
      strand: g.strand,
      color,
      kind: "protospacer",
    });
    features.push({
      chr: contig,
      start: g.pam_start,
      end: g.pam_end,
      name: `${tag} PAM ${g.pam_sequence}`,
      strand: g.strand,
      color: PAM_COLOR,
      kind: "pam",
    });
    const cut = g.edit_outcome_summary?.cut_position_fwd;
    if (typeof cut === "number") {
      features.push({
        chr: contig,
        start: cut,
        end: cut + 1,
        name: `${tag} cut`,
        strand: g.strand,
        color: CUT_COLOR,
        kind: "cut",
      });
    }
  }
  return features;
}

/**
 * igv.js `search()` locus string ("contig:start-end", 1-based inclusive) spanning a guide's
 * protospacer through its PAM, with a little flanking context. Converts the backend's 0-based
 * half-open coordinates (start inclusive, end exclusive) to igv's 1-based inclusive convention
 * (lo = start + 1, hi = end), so navigating to it lands on exactly the rendered feature. Used to
 * center the genome browser on a selected guide (linked selection).
 */
export function guideLocus(
  guide: Pick<
    GuideReport,
    "protospacer_start" | "protospacer_end" | "pam_start" | "pam_end"
  >,
  contig: string = TARGET_CONTIG,
  flank: number = 8,
): string {
  const start0 = Math.min(guide.protospacer_start, guide.pam_start);
  const end0 = Math.max(guide.protospacer_end, guide.pam_end);
  const lo = Math.max(1, start0 + 1 - flank);
  const hi = end0 + flank;
  return `${contig}:${lo}-${hi}`;
}

/** igv.js reference descriptor for an inline (non-indexed) single-contig FASTA. */
export interface IgvReferenceConfig {
  id: string;
  name: string;
  fastaURL: string;
  /** false => igv loads the whole FASTA into memory; fine for a short target, no .fai. */
  indexed: false;
}

/**
 * Assemble the full igv.createBrowser config for a guide view over `fastaURL`
 * (a blob/object URL the caller created from buildTargetFasta). Kept here so the
 * browser-config shape is tested too, leaving the component as a thin lazy-load shell.
 */
export function buildIgvConfig(
  report: CrisprEditReportOutput,
  fastaURL: string,
  contig: string = TARGET_CONTIG,
): {
  reference: IgvReferenceConfig;
  tracks: Array<Record<string, unknown>>;
  locus: string;
  showChromosomeWidget: boolean;
  showCenterGuide: boolean;
  loadDefaultGenomes: boolean;
} {
  return {
    reference: {
      id: contig,
      name: "Submitted target",
      fastaURL,
      indexed: false,
    },
    tracks: [
      {
        name: "Guides",
        type: "annotation",
        displayMode: "EXPANDED",
        height: 120,
        features: buildGuideFeatures(report, contig),
      },
    ],
    // Show the whole submitted contig by default.
    locus: contig,
    showChromosomeWidget: false,
    showCenterGuide: false,
    // The view uses the submitted sequence as its OWN reference and never offers a genome
    // dropdown, so suppress igv's startup fetch of its hosted default-genome registry. That
    // network call is pointless here and (when it times out, e.g. offline) leaves the viewer
    // empty — this keeps the guide view fully self-contained and offline-safe.
    loadDefaultGenomes: false,
  };
}
