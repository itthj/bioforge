import type { CalibrationCurve, ReliabilityCurve } from "../types/benchmarks";

export interface Prediction {
  id: string;
  project_id: string;
  subject_key: string;
  assay: string;
  kind: "probability" | "regression";
  predicted_value: number;
  source: string | null;
  observed_value: number | null;
  observed_at: string | null;
  outcome_note: string | null;
  created_at: string;
}

export interface PredictionIn {
  subject_key: string;
  assay: string;
  predicted_value: number;
  kind: "probability" | "regression";
  source?: string | null;
}

export interface AgreementResponse {
  project_id: string;
  assay: string;
  kind: "probability" | "regression";
  n_total: number;
  n_matched: number;
  n_pending: number;
  reliability: ReliabilityCurve | null;
  calibration: CalibrationCurve | null;
}

async function jsonOrThrow(res: Response) {
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(typeof err.detail === "string" ? err.detail : "Request failed");
  }
  return res.json();
}

export async function recordPredictions(projectId: string, predictions: PredictionIn[]): Promise<Prediction[]> {
  const res = await fetch("/predictions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_id: projectId, predictions }),
  });
  return jsonOrThrow(res);
}

export async function listPredictions(projectId: string): Promise<Prediction[]> {
  const res = await fetch(`/predictions?project_id=${encodeURIComponent(projectId)}`);
  return jsonOrThrow(res);
}

export async function recordOutcome(
  predictionId: string,
  observedValue: number,
  note?: string,
): Promise<Prediction> {
  const res = await fetch(`/predictions/${predictionId}/outcome`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ observed_value: observedValue, note: note ?? null }),
  });
  return jsonOrThrow(res);
}

export async function getAgreement(projectId: string, assay: string): Promise<AgreementResponse> {
  const res = await fetch(
    `/predictions/agreement?project_id=${encodeURIComponent(projectId)}&assay=${encodeURIComponent(assay)}`,
  );
  return jsonOrThrow(res);
}
