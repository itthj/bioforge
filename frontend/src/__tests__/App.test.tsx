/**
 * Tests for the top-level App state machine.
 *
 * Mocks the streaming API (`../api/agent`) and the projects API (`../api/projects`)
 * so the test exercises pure UI state transitions: idle → running → done, idle →
 * running → pending_approval → (approve) → done, plus the "New goal" reset.
 *
 * What we deliberately DON'T test here:
 *   - SSE wire-format parsing (covered by the backend suite + the agent.ts consumer
 *     logic — that runs against real backends, not vitest)
 *   - Project switcher dropdown behavior (separate test)
 *   - Memory inspector CRUD (separate test)
 *
 * The value is in pinning the state machine so future refactors of App.tsx can't
 * silently regress what the user sees in each phase.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import type {
  AgentDoneEvent,
  AgentStep,
  SseEvent,
} from "../types/agent";

// --- Mocks --------------------------------------------------------------------------

vi.mock("../api/agent", () => ({
  streamAgentRun: vi.fn(),
  streamAgentApprove: vi.fn(),
  cancelRun: vi.fn(),
}));

vi.mock("../api/projects", () => ({
  // Just return an empty list; the switcher renders the current project id as a fallback
  // when no Project matches. No project switching happens in these tests.
  listProjects: vi.fn().mockResolvedValue([]),
  createProject: vi.fn(),
  getProject: vi.fn(),
  updateProject: vi.fn(),
  deleteProject: vi.fn(),
  listMemory: vi.fn().mockResolvedValue([]),
  upsertMemory: vi.fn(),
  deleteMemory: vi.fn(),
  ApiError: class ApiError extends Error {
    status = 500;
    detail = "";
  },
}));

async function* mockStream(events: SseEvent[]): AsyncGenerator<SseEvent> {
  // Yield events in scripted order. Microtask hop after each yield gives React a
  // chance to flush state updates between events, mirroring the real SSE pacing.
  for (const ev of events) {
    yield ev;
    await Promise.resolve();
  }
}

function makeStep(idx: number, overrides: Partial<AgentStep> = {}): AgentStep {
  return {
    idx,
    type: "tool_call",
    duration_ms: 3,
    ...overrides,
  } as AgentStep;
}

function makeDone(overrides: Partial<AgentDoneEvent> = {}): AgentDoneEvent {
  return {
    trace_id: "trace_test",
    status: "completed",
    response_text: "All done.",
    model: "claude-sonnet-4-6",
    usage: {
      input_tokens: 100,
      output_tokens: 50,
      cache_creation_tokens: 0,
      cache_read_tokens: 0,
      cost_usd: 0.001,
      model: "claude-sonnet-4-6",
    },
    pending_plan: null,
    approval_reasons: [],
    ...overrides,
  };
}

// --- Tests --------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
});

describe("App state machine", () => {
  it("starts idle: chat input visible, no trace, no result", () => {
    render(<App />);

    expect(screen.getByPlaceholderText(/What do you want BioForge to do/i)).toBeInTheDocument();
    expect(screen.queryByText(/^Trace$/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Result$/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /New goal/i })).not.toBeInTheDocument();
  });

  it("happy path: submit → running → done → final card with response text", async () => {
    const { streamAgentRun } = await import("../api/agent");
    vi.mocked(streamAgentRun).mockImplementationOnce(() =>
      mockStream([
        { event: "step", data: makeStep(0, { type: "plan", plan: { is_trivial: true, summary: "trivial", steps: [] } }) },
        { event: "step", data: makeStep(1, { type: "tool_call", tool_name: "gc_content", tool_output: { gc_percent: 50 } }) },
        { event: "step", data: makeStep(2, { type: "final" }) },
        { event: "done", data: makeDone({ response_text: "GC content is 50%." }) },
      ]),
    );

    const user = userEvent.setup();
    render(<App />);

    await user.type(
      screen.getByPlaceholderText(/What do you want BioForge to do/i),
      "GC content of ATGC",
    );
    await user.click(screen.getByRole("button", { name: /^Send$/i }));

    // Final card surfaces the response text once the done event lands.
    await waitFor(() =>
      expect(screen.getByText(/GC content is 50%\./)).toBeInTheDocument(),
    );

    // Trace section renders the three streamed steps.
    expect(screen.getByText(/^Trace$/i)).toBeInTheDocument();
    expect(screen.getByText(/^Result$/i)).toBeInTheDocument();

    // The "Completed" status badge is shown on the result card.
    expect(screen.getByText(/^Completed$/i)).toBeInTheDocument();

    // streamAgentRun was called with the goal + default project_id + default autonomy
    // (plus an AbortSignal so the run can be stopped).
    expect(streamAgentRun).toHaveBeenCalledWith(
      {
        goal: "GC content of ATGC",
        projectId: "default-project",
        autonomy: "auto",
      },
      expect.anything(),
    );
  });

  it("review autonomy: selecting 'Review plan' sends autonomy=review and surfaces the plan", async () => {
    const { streamAgentRun } = await import("../api/agent");
    vi.mocked(streamAgentRun).mockImplementationOnce(() =>
      mockStream([
        { event: "step", data: makeStep(0, { type: "plan" }) },
        {
          event: "step",
          data: makeStep(1, {
            type: "approval_requested",
            approval_reasons: ["Review mode: you chose to approve the plan before any tools run."],
          }),
        },
        {
          event: "done",
          data: makeDone({
            status: "pending_approval",
            response_text: "Approval required before running this plan.",
            pending_plan: {
              is_trivial: false,
              summary: "GC of the reverse complement.",
              steps: [
                { idx: 0, description: "Reverse complement", expected_tool: "reverse_complement", rationale: "x" },
                { idx: 1, description: "GC content", expected_tool: "gc_content", rationale: "y" },
              ],
            },
            approval_reasons: ["Review mode: you chose to approve the plan before any tools run."],
          }),
        },
      ]),
    );

    const user = userEvent.setup();
    render(<App />);

    // Flip autonomy to review, then send.
    await user.click(screen.getByRole("button", { name: /Review plan/i }));
    await user.type(
      screen.getByPlaceholderText(/What do you want BioForge to do/i),
      "GC of rev comp of ATGC",
    );
    await user.click(screen.getByRole("button", { name: /^Send$/i }));

    expect(streamAgentRun).toHaveBeenCalledWith(
      {
        goal: "GC of rev comp of ATGC",
        projectId: "default-project",
        autonomy: "review",
      },
      expect.anything(),
    );

    // Even though no tool is expensive, the run paused for plan approval.
    await waitFor(() => expect(screen.getByText(/^Approval$/i)).toBeInTheDocument());
    expect(screen.getByText(/Review mode/i)).toBeInTheDocument();
  });

  it("pending_approval: shows ApprovalCard with the pending plan; clicking Approve resumes the stream", async () => {
    const { streamAgentRun, streamAgentApprove } = await import("../api/agent");

    vi.mocked(streamAgentRun).mockImplementationOnce(() =>
      mockStream([
        { event: "step", data: makeStep(0, { type: "plan" }) },
        { event: "step", data: makeStep(1, { type: "approval_requested", approval_reasons: ["blast: expensive"] }) },
        {
          event: "done",
          data: makeDone({
            status: "pending_approval",
            response_text: "Approval required before running this plan.",
            pending_plan: {
              is_trivial: false,
              summary: "BLAST against nt",
              steps: [
                { idx: 0, description: "BLAST the input", expected_tool: "blast", rationale: "find homologs" },
              ],
            },
            approval_reasons: ["blast: expensive (NCBI BLAST is paid in latency)"],
          }),
        },
      ]),
    );

    vi.mocked(streamAgentApprove).mockImplementationOnce(() =>
      mockStream([
        { event: "step", data: makeStep(3, { type: "tool_call", tool_name: "blast", tool_output: { hits: [] } }) },
        { event: "step", data: makeStep(4, { type: "final" }) },
        { event: "done", data: makeDone({ response_text: "BLAST returned 0 hits." }) },
      ]),
    );

    const user = userEvent.setup();
    render(<App />);

    await user.type(
      screen.getByPlaceholderText(/What do you want BioForge to do/i),
      "BLAST this sequence",
    );
    await user.click(screen.getByRole("button", { name: /^Send$/i }));

    // ApprovalCard appears (section header "Approval", not "Result")
    await waitFor(() => expect(screen.getByText(/^Approval$/i)).toBeInTheDocument());
    expect(screen.getByText(/Approval required/i)).toBeInTheDocument();
    expect(screen.getByText(/blast: expensive/i)).toBeInTheDocument();

    // Click Approve — streamAgentApprove is called with the trace id
    await user.click(screen.getByRole("button", { name: /^Approve$/i }));
    expect(streamAgentApprove).toHaveBeenCalledWith(
      { traceId: "trace_test", approved: true },
      expect.anything(),
    );

    // After resumption, the final response replaces the approval card
    await waitFor(() =>
      expect(screen.getByText(/BLAST returned 0 hits\./)).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: /^Approve$/i })).not.toBeInTheDocument();
  });

  it("celery mode: Stop revokes the worker via cancelRun with the queued trace_id", async () => {
    const { streamAgentRun, cancelRun } = await import("../api/agent");
    // A celery run announces its trace_id with a `queued` event, then streams; we leave it
    // hanging (never `done`) so the run stays in `running` and the Stop button is offered.
    vi.mocked(streamAgentRun).mockImplementationOnce(
      () =>
        (async function* () {
          yield {
            event: "queued",
            data: { trace_id: "trace_celery_1", status: "queued", job_backend: "celery" },
          };
          yield { event: "step", data: makeStep(0, { type: "plan" }) };
          await new Promise(() => {}); // run stays live until the user stops it
        })() as AsyncGenerator<SseEvent>,
    );

    const user = userEvent.setup();
    render(<App />);

    await user.type(
      screen.getByPlaceholderText(/What do you want BioForge to do/i),
      "a long celery run",
    );
    await user.click(screen.getByRole("button", { name: /^Send$/i }));

    const stopBtn = await screen.findByRole("button", { name: /Stop/i });
    await user.click(stopBtn);

    // The worker task is revoked by trace_id (inline runs, which emit no queued event, would not).
    expect(cancelRun).toHaveBeenCalledWith("trace_celery_1");
  });

  it("inline mode: Stop does NOT call cancelRun (no queued event, nothing to revoke)", async () => {
    const { streamAgentRun, cancelRun } = await import("../api/agent");
    vi.mocked(streamAgentRun).mockImplementationOnce(
      () =>
        (async function* () {
          yield { event: "step", data: makeStep(0, { type: "plan" }) };
          await new Promise(() => {});
        })() as AsyncGenerator<SseEvent>,
    );

    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByPlaceholderText(/What do you want BioForge to do/i), "an inline run");
    await user.click(screen.getByRole("button", { name: /^Send$/i }));
    const stopBtn = await screen.findByRole("button", { name: /Stop/i });
    await user.click(stopBtn);

    expect(cancelRun).not.toHaveBeenCalled();
  });

  it("New goal resets state: previous trace + result disappear, input is empty", async () => {
    const { streamAgentRun } = await import("../api/agent");
    vi.mocked(streamAgentRun).mockImplementationOnce(() =>
      mockStream([
        { event: "step", data: makeStep(0, { type: "final" }) },
        { event: "done", data: makeDone({ response_text: "first run" }) },
      ]),
    );

    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByPlaceholderText(/What do you want BioForge to do/i), "first goal");
    await user.click(screen.getByRole("button", { name: /^Send$/i }));
    await waitFor(() => expect(screen.getByText(/first run/)).toBeInTheDocument());

    // Now reset
    await user.click(screen.getByRole("button", { name: /New goal/i }));

    expect(screen.queryByText(/first run/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Trace$/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Result$/i)).not.toBeInTheDocument();
    // Chat input is back to empty + enabled
    const textarea = screen.getByPlaceholderText(
      /What do you want BioForge to do/i,
    ) as HTMLTextAreaElement;
    expect(textarea.value).toBe("");
    expect(textarea).not.toBeDisabled();
  });

  it("recovery: after an error, Retry re-runs the same goal", async () => {
    const { streamAgentRun } = await import("../api/agent");
    vi.mocked(streamAgentRun)
      .mockImplementationOnce(() => mockStream([{ event: "error", data: { message: "boom" } }]))
      .mockImplementationOnce(() =>
        mockStream([
          { event: "step", data: makeStep(0, { type: "final" }) },
          { event: "done", data: makeDone({ response_text: "recovered output" }) },
        ]),
      );

    const user = userEvent.setup();
    render(<App />);

    await user.type(
      screen.getByPlaceholderText(/What do you want BioForge to do/i),
      "GC content of ATGC",
    );
    await user.click(screen.getByRole("button", { name: /^Send$/i }));

    // Error is surfaced and a Retry control appears.
    await waitFor(() => expect(screen.getByText(/boom/)).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /Retry/i }));

    // The same goal was re-run and the second attempt's result lands.
    await waitFor(() => expect(screen.getByText(/recovered output/)).toBeInTheDocument());
    expect(streamAgentRun).toHaveBeenCalledTimes(2);
    expect(streamAgentRun).toHaveBeenLastCalledWith(
      { goal: "GC content of ATGC", projectId: "default-project", autonomy: "auto" },
      expect.anything(),
    );
  });
});
