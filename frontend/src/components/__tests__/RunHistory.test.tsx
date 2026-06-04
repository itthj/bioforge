/**
 * RunHistory tests — the run-history list (P0). Mocks the traces API and checks the
 * list renders, opens a run on click, and shows an empty state.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RunHistory } from "../RunHistory";
import type { TraceSummary } from "../../types/traces";

vi.mock("../../api/traces", () => ({
  listTraces: vi.fn(),
}));

import { listTraces } from "../../api/traces";

const RUNS: TraceSummary[] = [
  {
    trace_id: "t1",
    project_id: "p",
    goal: "Compute GC content of ATGC",
    status: "completed",
    model: "claude-sonnet-4-6",
    cost_usd: 0.0012,
    response_preview: "The GC content is 50%.",
    created_at: "2026-01-01T00:00:00Z",
    completed_at: "2026-01-01T00:00:01Z",
  },
];

beforeEach(() => vi.clearAllMocks());

describe("RunHistory", () => {
  it("lists runs and calls onOpen with the trace id when a run is clicked", async () => {
    vi.mocked(listTraces).mockResolvedValue(RUNS);
    const onOpen = vi.fn();
    const user = userEvent.setup();
    render(<RunHistory projectId="p" onOpen={onOpen} />);

    await waitFor(() =>
      expect(screen.getByText("Compute GC content of ATGC")).toBeInTheDocument(),
    );
    await user.click(screen.getByText("Compute GC content of ATGC"));
    expect(onOpen).toHaveBeenCalledWith("t1");
  });

  it("shows an empty state when the project has no runs", async () => {
    vi.mocked(listTraces).mockResolvedValue([]);
    render(<RunHistory projectId="p" onOpen={() => {}} />);
    await waitFor(() => expect(screen.getByText(/No runs yet/i)).toBeInTheDocument());
  });
});
