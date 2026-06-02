// Mirrors backend/src/bioforge/tools/sequence/crispr_edit_report.py output shape.
// Hand-written; changes to the backend Pydantic models surface here as type errors.

export type RecommendationLabel = "preferred" | "acceptable" | "caution" | "avoid";

export interface OutcomeSummary {
  cut_position_fwd: number;
  frameshift_probability: number;
  no_edit_probability: number;
  // The backend emits each outcome as a free-form dict matching edit_outcome's row
  // shape; we keep it loose here rather than re-mirroring every field.
  top_outcomes: Record<string, unknown>[];
}

export interface OfftargetSummary {
  searched: boolean;
  database: string | null;
  high_risk_count: number;
  medium_risk_count: number;
  low_risk_count: number;
  top_hits: Record<string, unknown>[];
  caveats: string[];
}

export interface GuideReport {
  rank: number;
  protospacer: string;
  pam_sequence: string;
  strand: "+" | "-";
  protospacer_start: number;
  protospacer_end: number;
  pam_start: number;
  pam_end: number;
  heuristic_score: number;
  on_target_score: number | null;
  recommendation_score: number;
  recommendation_label: RecommendationLabel;
  rationale: string[];
  off_target_summary: OfftargetSummary;
  edit_outcome_summary: OutcomeSummary | null;
}

export interface CrisprEditReportOutput {
  target_length: number;
  // The submitted target locus, echoed back (cleaned + uppercased). Coordinates on
  // each GuideReport are sequence-relative to THIS string only — not a genome build.
  // The IGV guide view renders it as its own reference. May be absent on traces
  // produced before this field existed, so consumers must tolerate undefined.
  target_sequence: string;
  pam: string;
  num_guides_considered: number;
  recommended_guide: GuideReport | null;
  guides: GuideReport[];
  tool_chain: string[];
  caveats: string[];
  // Provenance fields stamped by execute_tool — present on every tool output.
  tool_name?: string;
  tool_version?: string;
  citations?: string[];
}

// --- Off-target hits (mirrors find_offtargets.OfftargetHit, the fields the UI reads) ---
// In CrisprEditReportOutput these arrive inside off_target_summary.top_hits as loose dicts
// (Record<string, unknown>), so we coerce them with coerceOfftargetHits below.

export type RiskLabel = "high" | "medium" | "low";

/** A BLAST hit resolved to a GRCh38 locus (mirrors genomic_placement.GenomicPlacement). */
export interface GenomicPlacement {
  build: string;
  chromosome: string; // UCSC contig, e.g. "chr1"
  start: number; // 0-based, inclusive
  end: number; // 0-based, exclusive
  strand: "+" | "-";
  source_accession: string;
}

export interface OfftargetHit {
  accession: string;
  organism: string | null;
  subject_definition: string;
  mismatch_count: number;
  mit_score: number;
  cfd_mismatch_score: number | null;
  risk_label: RiskLabel;
  /** Present only when the hit sits on a GRCh38 primary chromosome; null otherwise. */
  genomic_placement: GenomicPlacement | null;
}

function asGenomicPlacement(raw: unknown): GenomicPlacement | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  if (
    typeof o.chromosome !== "string" ||
    typeof o.start !== "number" ||
    typeof o.end !== "number"
  ) {
    return null;
  }
  const strand = o.strand === "-" ? "-" : "+";
  return {
    build: typeof o.build === "string" ? o.build : "GRCh38",
    chromosome: o.chromosome,
    start: o.start,
    end: o.end,
    strand,
    source_accession:
      typeof o.source_accession === "string" ? o.source_accession : "",
  };
}

const RISK_LABELS: ReadonlySet<string> = new Set(["high", "medium", "low"]);

/** Coerce the loose top_hits dicts into typed OfftargetHits, tolerating missing fields. */
export function coerceOfftargetHits(
  raw: Record<string, unknown>[] | undefined,
): OfftargetHit[] {
  if (!raw) return [];
  return raw.map((h) => ({
    accession: typeof h.accession === "string" ? h.accession : "",
    organism: typeof h.organism === "string" ? h.organism : null,
    subject_definition:
      typeof h.subject_definition === "string" ? h.subject_definition : "",
    mismatch_count: typeof h.mismatch_count === "number" ? h.mismatch_count : 0,
    mit_score: typeof h.mit_score === "number" ? h.mit_score : 0,
    cfd_mismatch_score:
      typeof h.cfd_mismatch_score === "number" ? h.cfd_mismatch_score : null,
    risk_label: (typeof h.risk_label === "string" && RISK_LABELS.has(h.risk_label)
      ? h.risk_label
      : "low") as RiskLabel,
    genomic_placement: asGenomicPlacement(h.genomic_placement),
  }));
}

/** Narrow a tool_output blob to CrisprEditReportOutput if it looks like one. */
export function isCrisprEditReport(
  output: unknown,
): output is CrisprEditReportOutput {
  if (!output || typeof output !== "object") return false;
  const o = output as Record<string, unknown>;
  return (
    typeof o.target_length === "number" &&
    typeof o.pam === "string" &&
    Array.isArray(o.guides) &&
    Array.isArray(o.tool_chain)
  );
}
