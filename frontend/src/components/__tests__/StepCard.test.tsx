/**
 * Tests for the StepCard "validation" rendering — the §4/§6 trust signal the agent emits
 * (grounding status, OOD flags, model uncertainty) now surfaces in the trace instead of
 * being silently dropped. Content-based, like the other card tests.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StepCard } from "../StepCard";
import type { AgentStep, ValidationVerdict } from "../../types/agent";

function validationStep(verdict: Partial<ValidationVerdict> = {}): AgentStep {
  return {
    idx: 7,
    type: "validation",
    duration_ms: 12,
    verdict: {
      ok: true,
      summary: "All 3 numeric claims traced to tool outputs.",
      mode: "annotate",
      enforced: false,
      ood: { ok: true, checked: 1, flags: [] },
      model_uncertainty: [],
      ...verdict,
    },
  } as AgentStep;
}

describe("StepCard validation step", () => {
  it("renders a grounded verdict with the mode and summary", () => {
    render(<StepCard step={validationStep()} />);
    expect(screen.getByText(/grounded/i)).toBeInTheDocument();
    expect(screen.getByText(/annotate/)).toBeInTheDocument();
    expect(screen.getByText(/traced to tool outputs/i)).toBeInTheDocument();
  });

  it("flags unverifiable claims when not grounded", () => {
    render(<StepCard step={validationStep({ ok: false })} />);
    expect(screen.getByText(/unverifiable claim/i)).toBeInTheDocument();
  });

  it("surfaces OOD flags prominently", () => {
    const step = validationStep({
      ood: {
        ok: false,
        checked: 1,
        flags: [
          {
            tool: "find_offtargets",
            field: "guide",
            detail: "guide length 18 nt",
            envelope: "20 nt (SpCas9)",
            message: "extrapolation",
          },
        ],
      },
    });
    render(<StepCard step={step} />);
    expect(screen.getByText(/Out-of-distribution/i)).toBeInTheDocument();
    expect(screen.getByText(/guide length 18 nt/)).toBeInTheDocument();
  });

  it("lists model uncertainty notes", () => {
    const step = validationStep({
      model_uncertainty: [{ tool: "find_offtargets", score_key: "cfd_offtarget", note: "point estimate only" }],
    });
    render(<StepCard step={step} />);
    expect(screen.getByText(/model uncertainty/i)).toBeInTheDocument();
    expect(screen.getByText(/point estimate only/i)).toBeInTheDocument();
  });
});
