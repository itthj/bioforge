// Fetch wrapper for the Accuracy Report endpoint (§13 / §5). Reuses ApiError from the
// projects client for consistent error surfacing.

import type { AccuracyReport } from "../types/benchmarks";
import { ApiError } from "./projects";

export async function getAccuracyReport(): Promise<AccuracyReport> {
  const response = await fetch("/benchmarks/accuracy");
  if (response.ok) {
    return (await response.json()) as AccuracyReport;
  }
  let detail = response.statusText;
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") detail = body.detail;
  } catch {
    /* fall through with statusText */
  }
  throw new ApiError(response.status, detail);
}
