/**
 * Tests for ReliabilityDiagram.
 *
 * Asserts the honest §6 / rule 11 surface: the monotonicity rho, the per-bin points (predicted
 * vs measured, with SEM), the axis labels, and the verbatim "not a probability calibration"
 * caveat. We render only the bins the backend produced. Tested by content, not class names.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ReliabilityDiagram } from "../ReliabilityDiagram";
import type { ReliabilityCurve } from "../../types/benchmarks";

function makeCurve(overrides: Partial<ReliabilityCurve> = {}): ReliabilityCurve {
  return {
    n: 4,
    n_bins: 2,
    bins: [
      {
        bin_index: 0,
        n: 2,
        predicted_mean: 0.2,
        observed_mean: 0.1,
        observed_sem: 0.0,
        predicted_low: 0.1,
        predicted_high: 0.3,
      },
      {
        bin_index: 1,
        n: 2,
        predicted_mean: 0.8,
        observed_mean: 0.5,
        observed_sem: 0.05,
        predicted_low: 0.7,
        predicted_high: 0.9,
      },
    ],
    monotonicity_rho: 1.0,
    kind: "regression_ranking",
    predicted_label: "DeepCRISPR score",
    observed_label: "measured efficiency",
    caveat: "Ranking-reliability curve: the score is not a probability calibration; y=x is not the target.",
    ...overrides,
  };
}

describe("ReliabilityDiagram", () => {
  it("renders the monotonicity rho and the chart", () => {
    render(<ReliabilityDiagram curve={makeCurve()} />);
    expect(screen.getByText(/monotonicity/i)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /reliability curve/i })).toBeInTheDocument();
  });

  it("lists each bin's predicted and observed (± SEM) in the bin table", () => {
    render(<ReliabilityDiagram curve={makeCurve()} />);
    expect(screen.getByText("0.200")).toBeInTheDocument(); // bin 0 predicted_mean
    expect(screen.getByText("0.800")).toBeInTheDocument(); // bin 1 predicted_mean
    expect(screen.getByText("0.100 ± 0.000")).toBeInTheDocument(); // bin 0 observed ± SEM
    expect(screen.getByText("0.500 ± 0.050")).toBeInTheDocument(); // bin 1 observed ± SEM
  });

  it("labels both axes from the curve", () => {
    render(<ReliabilityDiagram curve={makeCurve()} />);
    expect(screen.getByText("DeepCRISPR score")).toBeInTheDocument();
    expect(screen.getByText("measured efficiency")).toBeInTheDocument();
  });

  it("shows the bin/prediction count and the honest not-a-probability caveat", () => {
    render(<ReliabilityDiagram curve={makeCurve()} />);
    expect(screen.getByText(/2 quantile bins over 4 predictions/i)).toBeInTheDocument();
    expect(screen.getByText(/not a probability calibration/i)).toBeInTheDocument();
  });
});
