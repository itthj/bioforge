/**
 * Tests for CalibrationDiagram.
 *
 * Asserts the honest §6 probability-calibration surface: ECE / MCE / Brier summary, per-bin
 * points (predicted probability vs observed frequency, with gap), and the CSV export. Tested by
 * content, not class names.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CalibrationDiagram, calibrationToCsv } from "../CalibrationDiagram";
import { downloadBlob } from "../../lib/download";
import type { CalibrationCurve } from "../../types/benchmarks";

vi.mock("../../lib/download", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../lib/download")>()),
  downloadBlob: vi.fn(),
}));

function makeCurve(overrides: Partial<CalibrationCurve> = {}): CalibrationCurve {
  return {
    n: 4,
    n_bins: 2,
    bins: [
      { bin_index: 0, n: 2, predicted_mean: 0.1, observed_freq: 0.0, gap: 0.1, bin_low: 0.0, bin_high: 0.5 },
      { bin_index: 1, n: 2, predicted_mean: 0.9, observed_freq: 0.5, gap: 0.4, bin_low: 0.5, bin_high: 1.0 },
    ],
    ece: 0.25,
    mce: 0.4,
    brier: 0.18,
    base_rate: 0.25,
    kind: "probability",
    predicted_label: "predicted probability",
    observed_label: "observed frequency",
    caveat: "y=x IS the target here.",
    ...overrides,
  };
}

describe("CalibrationDiagram", () => {
  it("shows ECE, MCE and Brier", () => {
    render(<CalibrationDiagram curve={makeCurve()} />);
    expect(screen.getByText(/ECE 0.250/)).toBeInTheDocument();
    expect(screen.getByText(/MCE 0.400/)).toBeInTheDocument();
    expect(screen.getByText(/Brier 0.180/)).toBeInTheDocument();
  });

  it("renders one table row per bin with the gap", () => {
    render(<CalibrationDiagram curve={makeCurve()} />);
    // The gap column for bin 1 = 0.400.
    expect(screen.getByText("0.400")).toBeInTheDocument();
    expect(screen.getByText(/y=x IS the target/)).toBeInTheDocument();
  });

  it("notes when the input is a squashed score, not a native probability", () => {
    render(<CalibrationDiagram curve={makeCurve({ kind: "squashed_score" })} />);
    expect(screen.getByText(/squashed score/)).toBeInTheDocument();
  });

  it("exports per-bin calibration data as CSV", async () => {
    render(<CalibrationDiagram curve={makeCurve()} />);
    await userEvent.click(screen.getByTitle(/Download the per-bin calibration data as CSV/));
    expect(downloadBlob).toHaveBeenCalled();
  });

  it("calibrationToCsv emits a header and one row per bin", () => {
    const csv = calibrationToCsv(makeCurve());
    const lines = csv.trim().split("\n");
    expect(lines[0]).toContain("predicted_mean");
    expect(lines).toHaveLength(3); // header + 2 bins
  });
});
