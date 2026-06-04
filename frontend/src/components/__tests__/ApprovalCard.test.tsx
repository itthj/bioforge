/**
 * Tests for ApprovalCard (P2b — edit the plan before approving).
 *
 * Verifies the review-mode surface: the proposed plan renders, the user can reword / reorder /
 * delete steps, Approve sends the edited plan only when changed (with idx renumbered to the
 * visible order), and the honest "steers, not constrains" framing is present. Tested by
 * content/behavior, not class names.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApprovalCard } from "../ApprovalCard";
import type { AgentDoneEvent, PlanPayload, PlanStep } from "../../types/agent";

function makeStep(overrides: Partial<PlanStep> = {}): PlanStep {
  return {
    idx: 0,
    description: "BLAST the query against nt.",
    expected_tool: "blast",
    rationale: "Find homologs.",
    ...overrides,
  };
}

function makeDone(overrides: Partial<AgentDoneEvent> = {}): AgentDoneEvent {
  const plan: PlanPayload = {
    is_trivial: false,
    summary: "Search for homologs, then summarize.",
    steps: [
      makeStep({ idx: 0, description: "Step A", expected_tool: "blast" }),
      makeStep({ idx: 1, description: "Step B", expected_tool: "gc_content" }),
    ],
  };
  return {
    trace_id: "t-1",
    status: "pending_approval",
    response_text: "",
    model: "claude",
    usage: null,
    pending_plan: plan,
    approval_reasons: ["Step 0 (blast): expensive — long runtime."],
    ...overrides,
  };
}

describe("ApprovalCard", () => {
  it("renders nothing when the run is not awaiting approval", () => {
    const { container } = render(
      <ApprovalCard done={makeDone({ status: "completed" })} onDecision={() => {}} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the proposed steps, reasons, and the honest steering note", () => {
    render(<ApprovalCard done={makeDone()} onDecision={() => {}} />);
    expect(screen.getByText(/Approval required/i)).toBeInTheDocument();
    expect((screen.getByLabelText("Step 1 description") as HTMLInputElement).value).toBe("Step A");
    expect((screen.getByLabelText("Step 2 description") as HTMLInputElement).value).toBe("Step B");
    expect(screen.getByText(/expensive — long runtime/i)).toBeInTheDocument();
    // Honesty: editing steers, it does not hard-constrain the free-form executor.
    expect(screen.getByText(/not a hard constraint on which tools run/i)).toBeInTheDocument();
  });

  it("approves the plan as-is (no edited plan) when nothing is changed", async () => {
    const onDecision = vi.fn();
    render(<ApprovalCard done={makeDone()} onDecision={onDecision} />);
    await userEvent.click(screen.getByRole("button", { name: /^approve$/i }));
    expect(onDecision).toHaveBeenCalledTimes(1);
    expect(onDecision).toHaveBeenCalledWith(true, undefined);
  });

  it("sends the edited plan when a step description is reworded", async () => {
    const onDecision = vi.fn();
    render(<ApprovalCard done={makeDone()} onDecision={onDecision} />);
    const input = screen.getByLabelText("Step 1 description");
    await userEvent.clear(input);
    await userEvent.type(input, "Reworded step A");
    // The button relabels to signal an edit.
    await userEvent.click(screen.getByRole("button", { name: /approve edited plan/i }));

    const [approved, plan] = onDecision.mock.calls[0];
    expect(approved).toBe(true);
    expect(plan.steps[0].description).toBe("Reworded step A");
    expect(plan.steps.map((s: PlanStep) => s.idx)).toEqual([0, 1]); // idx renumbered to order
  });

  it("deletes a step and renumbers idx on approve", async () => {
    const onDecision = vi.fn();
    render(<ApprovalCard done={makeDone()} onDecision={onDecision} />);
    await userEvent.click(screen.getByRole("button", { name: /delete step 1/i }));
    await userEvent.click(screen.getByRole("button", { name: /approve edited plan/i }));

    const plan: PlanPayload = onDecision.mock.calls[0][1];
    expect(plan.steps).toHaveLength(1);
    expect(plan.steps[0].description).toBe("Step B");
    expect(plan.steps[0].idx).toBe(0);
  });

  it("reorders steps and renumbers idx on approve", async () => {
    const onDecision = vi.fn();
    render(<ApprovalCard done={makeDone()} onDecision={onDecision} />);
    await userEvent.click(screen.getByRole("button", { name: /move step 2 up/i }));
    await userEvent.click(screen.getByRole("button", { name: /approve edited plan/i }));

    const plan: PlanPayload = onDecision.mock.calls[0][1];
    expect(plan.steps.map((s) => s.description)).toEqual(["Step B", "Step A"]);
    expect(plan.steps.map((s) => s.idx)).toEqual([0, 1]);
  });

  it("disables Approve when all steps are removed (nothing to run)", async () => {
    render(<ApprovalCard done={makeDone()} onDecision={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /delete step 1/i }));
    await userEvent.click(screen.getByRole("button", { name: /delete step 1/i }));
    expect(screen.getByText(/there is nothing to run/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /approve edited plan/i })).toBeDisabled();
  });

  it("cancels with no plan payload", async () => {
    const onDecision = vi.fn();
    render(<ApprovalCard done={makeDone()} onDecision={onDecision} />);
    await userEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    expect(onDecision).toHaveBeenCalledWith(false);
  });
});
