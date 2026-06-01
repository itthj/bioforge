/**
 * Tests for OnTargetScoreCard.
 *
 * Verifies the honest uncertainty surface (rule 10 / §6): only the scorers the backend actually
 * returned are shown, the point-estimate framing is present, and when multiple scorers are
 * present their disagreement is surfaced as a signal. We test by content, not class names.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OnTargetScoreCard } from "../OnTargetScoreCard";
import type { ScoreGuideOnTargetOutput } from "../../types/on_target";

function makeOutput(
  overrides: Partial<ScoreGuideOnTargetOutput> = {},
): ScoreGuideOnTargetOutput {
  return {
    protospacer: "GAGTCCGAGCAGAAGAAGAA",
    pam: "AGG",
    on_target_score: 0.512,
    score_breakdown: {
      gc_component: 0.5,
      polyt_component: 1.0,
      position_component: 0.6,
      dinucleotide_component: 0.5,
      component_weights: { gc: 0.3, polyt: 0.2, position: 0.35, dinucleotide: 0.15 },
    },
    deepcrispr_on_target_score: null,
    deepcrispr_model_version: null,
    azimuth_rs2_on_target_score: null,
    azimuth_rs2_model_version: null,
    caveats: ["on_target_score is a transparent rule-based proxy of published design rules."],
    ...overrides,
  };
}

describe("OnTargetScoreCard", () => {
  it("renders the rule-based score with the point-estimate framing", () => {
    render(<OnTargetScoreCard output={makeOutput()} />);
    expect(screen.getByText("0.512")).toBeInTheDocument();
    expect(screen.getByText(/point estimate/i)).toBeInTheDocument();
    // Single-scorer case names model-level published accuracy as the honest measure.
    expect(screen.getByText(/published accuracy/i)).toBeInTheDocument();
  });

  it("shows only the scorers the backend returned", () => {
    render(<OnTargetScoreCard output={makeOutput()} />);
    expect(screen.queryByText(/DeepCRISPR/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Rule Set 2/i)).not.toBeInTheDocument();
  });

  it("renders all three scorers side-by-side and surfaces disagreement as a signal", () => {
    render(
      <OnTargetScoreCard
        output={makeOutput({
          deepcrispr_on_target_score: 0.71,
          deepcrispr_model_version: "deepcrispr-ontar-cnn-reg-seq@abc",
          azimuth_rs2_on_target_score: 0.49,
          azimuth_rs2_model_version: "V3_model_nopos@dbd30b9",
        })}
      />,
    );
    expect(screen.getByText("0.512")).toBeInTheDocument();
    expect(screen.getByText("0.710")).toBeInTheDocument();
    expect(screen.getByText("0.490")).toBeInTheDocument();
    // The model name also appears in the version tag, so match all occurrences.
    expect(screen.getAllByText(/DeepCRISPR/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/Rule Set 2/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/compare guide rankings/i)).toBeInTheDocument();
    expect(screen.getByText(/disagreement/i)).toBeInTheDocument();
  });

  it("renders the caveats panel", () => {
    render(<OnTargetScoreCard output={makeOutput()} />);
    expect(screen.getByText("Caveats")).toBeInTheDocument();
    expect(screen.getByText(/transparent rule-based proxy/i)).toBeInTheDocument();
  });
});
