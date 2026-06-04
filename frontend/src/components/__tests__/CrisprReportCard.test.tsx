/**
 * Tests for CrisprReportCard.
 *
 * The component renders a structured biological report — the value of the tests is
 * verifying that the right information surfaces (recommendation label, scores,
 * caveats, off-target counts), not styling. We test by content, not by class names.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CrisprReportCard, guidesToCsv } from "../CrisprReportCard";
import { downloadBlob } from "../../lib/download";
import type { CrisprEditReportOutput, GuideReport } from "../../types/crispr";

// Keep the real serializer (toCsv) so we assert the actual CSV content; stub only the
// download side-effect so no file is written during the test.
vi.mock("../../lib/download", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../lib/download")>()),
  downloadBlob: vi.fn(),
}));

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

  it("builds a guide CSV with a header and one row per guide", () => {
    const csv = guidesToCsv(makeReport());
    const lines = csv.split("\r\n");
    expect(lines[0]).toMatch(/^rank,protospacer,pam,strand,/);
    // makeReport has 2 candidate guides -> header + 2 rows.
    expect(lines).toHaveLength(3);
    expect(lines[1]).toMatch(/^1,ACGTACGTACGTACGTACGG,AGG,\+,10,30,preferred/);
  });

  it("exports the guide table as CSV when the export button is clicked", async () => {
    vi.mocked(downloadBlob).mockClear();
    render(<CrisprReportCard report={makeReport()} />);
    await userEvent.click(screen.getByRole("button", { name: /export csv/i }));
    expect(downloadBlob).toHaveBeenCalledTimes(1);
    const [filename, mime, data] = vi.mocked(downloadBlob).mock.calls[0];
    expect(filename).toBe("crispr_guides.csv");
    expect(mime).toContain("text/csv");
    expect(String(data)).toMatch(/rank,protospacer,pam/);
  });

  it("toggles a guide's selected (pressed) state when its row is clicked", async () => {
    render(<CrisprReportCard report={makeReport()} />);
    const guideButtons = () => screen.getAllByTitle(/center this guide/i);

    expect(guideButtons().length).toBeGreaterThanOrEqual(1);
    // Nothing is selected on first render.
    expect(
      guideButtons().every((b) => b.getAttribute("aria-pressed") === "false"),
    ).toBe(true);

    await userEvent.click(guideButtons()[0]);
    // The clicked guide is now pressed (rank 1 appears in both the recommended card and the
    // candidate list, so both instances reflect the selection).
    expect(
      guideButtons().some((b) => b.getAttribute("aria-pressed") === "true"),
    ).toBe(true);

    // Clicking it again clears the selection (toggle).
    await userEvent.click(guideButtons()[0]);
    expect(
      guideButtons().every((b) => b.getAttribute("aria-pressed") === "false"),
    ).toBe(true);
  });
});
