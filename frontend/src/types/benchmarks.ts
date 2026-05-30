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

export interface AccuracyReport {
  generated_at: string;
  bioforge_version: string;
  validator: ValidatorGate;
  models: ModelAccuracyEntry[];
  benchmarks: BenchmarkStatus[];
}
