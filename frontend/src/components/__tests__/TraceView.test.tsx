/**
 * Tests for TraceView + the StepCard routing it uses.
 *
 * TraceView is mostly a list wrapper, but it's the surface where every step type lands.
 * The high-value assertions:
 *   - Empty state shows a placeholder, not just nothing
 *   - Each step renders with its type and index visible
 *   - tool_call steps with tool_name=crispr_edit_report route to the rich card,
 *     not the default JSON-blob fallback
 */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TraceView } from "../TraceView";
import type { AgentStep } from "../../types/agent";

function step(overrides: Partial<AgentStep> & Pick<AgentStep, "idx" | "type">): AgentStep {
  return {
    duration_ms: 5,
    ...overrides,
  } as AgentStep;
}

describe("TraceView", () => {
  it("shows an empty-state placeholder when there are no steps", () => {
    render(<TraceView steps={[]} />);
    expect(
      screen.getByText(/Steps will stream here as the agent runs/i),
    ).toBeInTheDocument();
  });

  it("renders a plan step with its summary and tool list", () => {
    const steps: AgentStep[] = [
      step({
        idx: 0,
        type: "plan",
        plan: {
          is_trivial: false,
          summary: "Translate and compute GC content",
          steps: [
            {
              idx: 0,
              description: "Translate DNA → protein",
              expected_tool: "translate",
              rationale: "needed for downstream analysis",
            },
            {
              idx: 1,
              description: "Compute GC of the result",
              expected_tool: "gc_content",
              rationale: "answers the user's question",
            },
          ],
        },
      }),
    ];
    render(<TraceView steps={steps} />);

    expect(screen.getByText(/Translate and compute GC content/)).toBeInTheDocument();
    expect(screen.getByText(/\[translate\]/)).toBeInTheDocument();
    expect(screen.getByText(/\[gc_content\]/)).toBeInTheDocument();
  });

  it("renders a tool_call step with the tool name visible", () => {
    const steps: AgentStep[] = [
      step({
        idx: 1,
        type: "tool_call",
        tool_name: "gc_content",
        tool_input: { sequence: "ATGC" },
        tool_output: { gc_percent: 50, total_length: 4 },
      }),
    ];
    render(<TraceView steps={steps} />);
    // The badge has "1. tool_call" and the body has "gc_content" — both are useful.
    expect(screen.getByText(/^gc_content$/)).toBeInTheDocument();
    expect(screen.getByText(/1\. tool_call/)).toBeInTheDocument();
  });

  it("routes crispr_edit_report tool output to the rich card", () => {
    const steps: AgentStep[] = [
      step({
        idx: 2,
        type: "tool_call",
        tool_name: "crispr_edit_report",
        tool_input: { target: "ATGCATGCATGCATGCATGC" },
        tool_output: {
          target_length: 60,
          pam: "NGG",
          num_guides_considered: 1,
          tool_chain: ["design_guides"],
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
              on_target_score: 0.75,
              recommendation_score: 0.78,
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
          recommended_guide: null,
          caveats: ["Test caveat"],
        },
      }),
    ];
    render(<TraceView steps={steps} />);

    // The rich card surfaces the "CRISPR edit report" header — present iff the
    // routing in StepCard worked. The default JSON-details fallback would NOT
    // contain this label as visible text.
    expect(screen.getByText(/CRISPR edit report/i)).toBeInTheDocument();
  });

  it("falls back to JSON details for unknown tools", () => {
    const steps: AgentStep[] = [
      step({
        idx: 3,
        type: "tool_call",
        tool_name: "gc_content",
        tool_input: { sequence: "ATGC" },
        tool_output: { gc_percent: 50, total_length: 4 },
      }),
    ];
    render(<TraceView steps={steps} />);

    // The fallback renders a <details> summary labelled "output".
    const outputDetails = screen.getByText(/^output$/i);
    expect(outputDetails).toBeInTheDocument();
    // And its parent <details> has the serialized JSON inside.
    const details = outputDetails.closest("details");
    expect(details).not.toBeNull();
    expect(within(details!).getByText(/gc_percent/)).toBeInTheDocument();
  });
});
