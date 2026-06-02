// Mirror of bioforge.benchmarks.accuracy_report (the GET /benchmarks/accuracy payload).
// Kept in sync by hand — the backend Pydantic models are the source of truth.

export interface CorpusMetrics {
  n_cases: number;
  numeric_true_positives: number;
  numeric_false_positives: number;
  numeric_false_negatives: number;
  numeric_block_precision: number;
  numeric_fabrication_recall: number;
  entity_true_positives: number;
  entity_false_positives: number;
  entity_false_negatives: number;
  entity_block_precision: number;
  entity_fabrication_recall: number;
}

export interface ValidatorGate {
  metrics: CorpusMetrics;
  threshold: number;
  numeric_passes: boolean;
  entity_passes: boolean;
  passes: boolean;
}

export interface ModelAccuracyEntry {
  tool: string;
  model_versions: Record<string, string>;
  published_accuracy: Record<string, string>;
  emits_instance_uncertainty: Record<string, boolean>;
}

export type BenchmarkWiring = "live" | "guard_only" | "not_yet_wired";

export interface BenchmarkStatus {
  name: string;
  blueprint_section: string;
  status: BenchmarkWiring;
  detail: string;
}

// Reliability (calibration) curve — mirror of bioforge.benchmarks.reliability.
// kind="regression_ranking" means the score is NOT a probability: read it for monotonicity
// (do higher scores track higher measured outcomes), not for absolute y=x agreement.
export interface ReliabilityBin {
  bin_index: number;
  n: number;
  predicted_mean: number;
  observed_mean: number;
  observed_sem: number;
  predicted_low: number;
  predicted_high: number;
}

export interface ReliabilityCurve {
  n: number;
  n_bins: number;
  bins: ReliabilityBin[];
  monotonicity_rho: number;
  kind: "regression_ranking" | "probability_calibration";
  predicted_label: string;
  observed_label: string;
  caveat: string;
}

// A real, dated benchmark measurement generated offline and published in the report (§13).
// Mirror of bioforge.benchmarks.published.PublishedBenchmark.
export interface PublishedBenchmark {
  name: string;
  blueprint_section: string;
  generated_at: string;
  model_version: string;
  dataset: string;
  data_sha256: string;
  citation: string;
  n: number;
  spearman_rho: number;
  pearson_r: number;
  leakage_status: string;
  leakage_evidence: string;
  leakage_caveat: string;
  dataset_relationship: string;
  interpretation: string;
  reliability: ReliabilityCurve;
}

export interface AccuracyReport {
  generated_at: string;
  bioforge_version: string;
  validator: ValidatorGate;
  models: ModelAccuracyEntry[];
  benchmarks: BenchmarkStatus[];
  // Real, dated measurements generated offline (a benchmark run is a network fetch + a Docker
  // call, never on page load). Each carries the reliability curve behind it. Empty until a run
  // is published.
  published: PublishedBenchmark[];
}
