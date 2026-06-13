/**
 * Tests for FeedbackPanel (wet-lab feedback loop).
 *
 * The api/predictions module is mocked so we test the panel's behaviour: it lists predictions,
 * records an outcome for an open one, and renders the agreement curve + the honest matched/pending
 * counts. Tested by content.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackPanel } from "../FeedbackPanel";
import * as api from "../../api/predictions";

vi.mock("../../api/predictions");

const mockApi = vi.mocked(api);

function pred(overrides: Partial<api.Prediction> = {}): api.Prediction {
  return {
    id: "p1",
    project_id: "proj",
    subject_key: "GUIDE_A",
    assay: "on-target",
    kind: "regression",
    predicted_value: 0.8,
    source: null,
    observed_value: null,
    observed_at: null,
    outcome_note: null,
    created_at: "2026-06-13T00:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockApi.listPredictions.mockResolvedValue([pred()]);
  mockApi.recordOutcome.mockResolvedValue(pred({ observed_value: 0.65 }));
  mockApi.getAgreement.mockResolvedValue({
    project_id: "proj",
    assay: "on-target",
    kind: "regression",
    n_total: 3,
    n_matched: 2,
    n_pending: 1,
    reliability: {
      n: 2,
      n_bins: 2,
      bins: [
        { bin_index: 0, n: 1, predicted_mean: 0.3, observed_mean: 0.2, observed_sem: 0, predicted_low: 0.3, predicted_high: 0.3 },
        { bin_index: 1, n: 1, predicted_mean: 0.8, observed_mean: 0.7, observed_sem: 0, predicted_low: 0.8, predicted_high: 0.8 },
      ],
      monotonicity_rho: 1.0,
      kind: "regression_ranking",
      predicted_label: "predicted on-target",
      observed_label: "measured outcome",
      caveat: "ranking only",
    },
    calibration: null,
  });
});

describe("FeedbackPanel", () => {
  it("lists predictions awaiting an outcome", async () => {
    render(<FeedbackPanel projectId="proj" />);
    expect(await screen.findByText("GUIDE_A")).toBeInTheDocument();
  });

  it("records an outcome for an open prediction", async () => {
    render(<FeedbackPanel projectId="proj" />);
    await screen.findByText("GUIDE_A");
    const input = screen.getByPlaceholderText("0.65");
    await userEvent.type(input, "0.7");
    await userEvent.click(screen.getByRole("button", { name: "save" }));
    await waitFor(() => expect(mockApi.recordOutcome).toHaveBeenCalledWith("p1", 0.7));
  });

  it("shows agreement counts and the reliability curve when an assay is selected", async () => {
    render(<FeedbackPanel projectId="proj" />);
    await screen.findByText("GUIDE_A");
    // Click the assay button in the agreement section.
    await userEvent.click(screen.getByRole("button", { name: "on-target" }));
    expect(await screen.findByText(/2 of 3 predictions have a measured outcome/)).toBeInTheDocument();
    expect(screen.getByText(/1 pending/)).toBeInTheDocument();
    // The reliability diagram renders its heading.
    expect(screen.getByText(/Reliability curve/)).toBeInTheDocument();
  });
});
