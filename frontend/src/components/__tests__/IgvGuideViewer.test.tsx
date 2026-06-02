/**
 * Tests for IgvGuideViewer.
 *
 * igv.js is mocked here so the lazy-load shell is exercised deterministically and
 * fast (the real ~3 MB browser is never instantiated in happy-dom). The coordinate
 * logic it feeds igv lives in igvGuideTrack.test.ts.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { IgvGuideViewer } from "../IgvGuideViewer";
import type { CrisprEditReportOutput } from "../../types/crispr";

const { createBrowserMock, removeBrowserMock } = vi.hoisted(() => ({
  createBrowserMock: vi.fn(),
  removeBrowserMock: vi.fn(),
}));

vi.mock("igv", () => ({
  default: { createBrowser: createBrowserMock, removeBrowser: removeBrowserMock },
}));

beforeEach(() => {
  createBrowserMock.mockReset();
  removeBrowserMock.mockReset();
  // happy-dom may not implement blob URLs; stub them so the component is deterministic.
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:fake-target");
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

function makeReport(
  overrides: Partial<CrisprEditReportOutput> = {},
): CrisprEditReportOutput {
  return {
    target_length: 33,
    target_sequence: "A".repeat(10) + "ACGTACGTACGTACGTACGG" + "AGG",
    pam: "NGG",
    num_guides_considered: 1,
    recommended_guide: null,
    guides: [
      {
        rank: 1,
        protospacer: "ACGTACGTACGTACGTACGG",
        pam_sequence: "AGG",
        strand: "+",
        protospacer_start: 10,
        protospacer_end: 30,
        pam_start: 30,
        pam_end: 33,
        heuristic_score: 0.8,
        on_target_score: 0.7,
        recommendation_score: 0.75,
        recommendation_label: "preferred",
        rationale: [],
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
      },
    ],
    tool_chain: ["design_guides"],
    caveats: [],
    ...overrides,
  };
}

describe("IgvGuideViewer", () => {
  it("renders the Load affordance and an honest 'not a genome build' note", () => {
    render(<IgvGuideViewer report={makeReport()} />);
    expect(
      screen.getByRole("button", { name: /load genome browser/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/not a genome build/i)).toBeInTheDocument();
    expect(screen.getByText(/1 guide\b/i)).toBeInTheDocument();
  });

  it("falls back when the report carries no target sequence", () => {
    render(<IgvGuideViewer report={makeReport({ target_sequence: "" })} />);
    expect(screen.getByText(/no target sequence/i)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /load genome browser/i }),
    ).not.toBeInTheDocument();
  });

  it("creates a browser with an inline, non-indexed reference on Load", async () => {
    createBrowserMock.mockResolvedValue({ dispose: vi.fn() });
    render(<IgvGuideViewer report={makeReport()} />);
    await userEvent.click(
      screen.getByRole("button", { name: /load genome browser/i }),
    );

    await waitFor(() => expect(screen.getByText(/^Loaded$/)).toBeInTheDocument());
    expect(createBrowserMock).toHaveBeenCalledTimes(1);
    const config = createBrowserMock.mock.calls[0][1] as {
      reference: { fastaURL: string; indexed: boolean };
      tracks: Array<{ features: unknown[] }>;
    };
    expect(config.reference.indexed).toBe(false);
    expect(config.reference.fastaURL).toBe("blob:fake-target");
    // protospacer + PAM for the single guide (no cut: edit outcome not simulated).
    expect(config.tracks[0].features).toHaveLength(2);
  });

  it("degrades gracefully when igv.js fails to initialize", async () => {
    createBrowserMock.mockRejectedValue(new Error("no canvas in this env"));
    render(<IgvGuideViewer report={makeReport()} />);
    await userEvent.click(
      screen.getByRole("button", { name: /load genome browser/i }),
    );
    await waitFor(() =>
      expect(screen.getByText(/igv\.js failed to render/i)).toBeInTheDocument(),
    );
    expect(
      screen.getByRole("button", { name: /retry/i }),
    ).toBeInTheDocument();
  });
});
