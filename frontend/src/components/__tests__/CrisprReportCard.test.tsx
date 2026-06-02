/**
 * Tests for CrisprReportCard.
 *
 * The component renders a structured biological report — the value of the tests is
 * verifying that the right information surfaces (recommendation label, scores,
 * caveats, off-target counts), not styling. We test by content, not by class names.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CrisprReportCard } from "../CrisprReportCard";
import type { CrisprEditReportOutput, GuideReport } from "../../types/crispr";

function makeGuide(overrides: Partial<GuideReport> = {}): GuideReport {
  return {
    rank: 1,
    protospacer: "ACGTACGTACGTACGTACGG",
    pam_sequence: "AGG",
    strand: "+",
    protospacer_start: 10,
    protospacer_end: 30,
    pam_start: 30,
    pam_end: 33,
    heuristic_score: 0.82,
    on_target_score: 0.74,
    recommendation_score: 0.78,
    recommendation_label: "preferred",
    rationale: ["High on-target score", "No polyT runs"],
    off_target_summary: {
      searched: false,
      database: null,
      high_risk_count: 0,
      medium_risk_count: 0,
      low_risk_count: 0,
      top_hits: [],
      caveats: [],
    },
    edit_outcome_summary: null,
    ...overrides,
  };
}

function makeReport(
  overrides: Partial<CrisprEditReportOutput> = {},
): CrisprEditReportOutput {
  return {
    target_length: 60,
    // Coordinate-consistent with makeGuide(): [10,30) is the protospacer, [30,33) the AGG PAM.
    target_sequence:
      "A".repeat(10) + "ACGTACGTACGTACGTACGG" + "AGG" + "C".repeat(27),
    pam: "NGG",
    num_guides_considered: 3,
    recommended_guide: makeGuide(),
    guides: [makeGuide(), makeGuide({ rank: 2, recommendation_label: "acceptable" })],
    tool_chain: ["design_guides", "score_guide_on_target", "edit_outcome"],
    caveats: ["Probabilities are literature averages, not per-guide predictions."],
    ...overrides,
  };
}

describe("CrisprReportCard", () => {
  it("renders the header with target length, PAM, and tool chain", () => {
    render(<CrisprReportCard report={makeReport()} />);

    expect(screen.getByText(/CRISPR edit report/i)).toBeInTheDocument();
    // Header line concatenates several facts; checking substrings keeps the test
    // robust to formatting tweaks.
    const header = screen.getByText(/target 60 nt/i);
    expect(header.textContent).toMatch(/PAM NGG/);
    expect(header.textContent).toMatch(/3 candidates/);
    expect(header.textContent).toMatch(
      /design_guides → score_guide_on_target → edit_outcome/,
    );
  });

  it("highlights the recommended guide with its label", () => {
    render(<CrisprReportCard report={makeReport()} />);

    expect(screen.getByText(/^Recommended$/i)).toBeInTheDocument();
    // The recommendation label badge shows up exactly twice: once on the recommended
    // guide card, once in the "all candidates" collapsed list. Both are valid.
    expect(screen.getAllByText("preferred").length).toBeGreaterThanOrEqual(1);
  });

  it("shows '—' for guides without an on-target score", () => {
    const guideNoScore = makeGuide({ on_target_score: null });
    render(<CrisprReportCard report={makeReport({ recommended_guide: guideNoScore, guides: [guideNoScore] })} />);

    // 'on-target' label appears once; its sibling value is the em-dash placeholder.
    const labels = screen.getAllByText(/^on-target$/i);
    expect(labels.length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(1);
  });

  it("renders the empty-state message when no guide is recommended", () => {
    render(<CrisprReportCard report={makeReport({ recommended_guide: null })} />);

    expect(
      screen.getByText(/No guide met the recommendation criteria/i),
    ).toBeInTheDocument();
  });

  it("renders edit-outcome and off-target summaries when present", () => {
    const guide = makeGuide({
      edit_outcome_summary: {
        cut_position_fwd: 27,
        frameshift_probability: 0.42,
        no_edit_probability: 0.5,
        top_outcomes: [],
      },
      off_target_summary: {
        searched: true,
        database: "nt",
        high_risk_count: 0,
        medium_risk_count: 2,
        low_risk_count: 7,
        top_hits: [],
        caveats: [],
      },
    });
    render(<CrisprReportCard report={makeReport({ recommended_guide: guide, guides: [guide] })} />);

    // The component renders the recommended guide AND the collapsed all-candidates
    // list (which still mounts the same content under a closed <details>). So each
    // assertion-by-text matches twice — `getAllByText` is the honest matcher.
    expect(screen.getAllByText(/frameshift 42%/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/no-edit 50%/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/Off-target/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/0 high/i).length).toBeGreaterThanOrEqual(1);
  });

  it("renders the caveats panel", () => {
    render(<CrisprReportCard report={makeReport()} />);

    expect(screen.getByText("Caveats")).toBeInTheDocument();
    expect(
      screen.getByText(/Probabilities are literature averages/i),
    ).toBeInTheDocument();
  });
});
