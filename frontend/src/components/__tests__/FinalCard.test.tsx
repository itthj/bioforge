/**
 * FinalCard tests — focused on the result rendering and the provenance export links
 * that surface the backend's /traces/{id}/{report,ro-crate,manifest} endpoints.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FinalCard } from "../FinalCard";
import type { AgentDoneEvent } from "../../types/agent";

function done(over: Partial<AgentDoneEvent> = {}): AgentDoneEvent {
  return {
    trace_id: "trace_abc",
    status: "completed",
    response_text: "GC content is 50%.",
    model: "claude-sonnet-4-6",
    usage: null,
    pending_plan: null,
    approval_reasons: [],
    ...over,
  };
}

describe("FinalCard", () => {
  it("renders the response text and the status badge", () => {
    render(<FinalCard done={done()} />);
    expect(screen.getByText(/GC content is 50%\./)).toBeInTheDocument();
    expect(screen.getByText(/^Completed$/)).toBeInTheDocument();
  });

  it("surfaces provenance export links pointing at the trace endpoints", () => {
    render(<FinalCard done={done({ trace_id: "trace_xyz" })} />);

    const reproduce = screen.getByRole("link", { name: /Reproduce/i });
    expect(reproduce).toHaveAttribute("href", "/traces/trace_xyz/script");
    expect(reproduce).toHaveAttribute("download");

    const report = screen.getByRole("link", { name: /Methods report/i });
    expect(report).toHaveAttribute("href", "/traces/trace_xyz/report");
    expect(report).toHaveAttribute("download");

    expect(screen.getByRole("link", { name: /RO-Crate/i })).toHaveAttribute(
      "href",
      "/traces/trace_xyz/ro-crate",
    );

    // The manifest is inline JSON, so it opens in a new tab rather than downloading.
    const manifest = screen.getByRole("link", { name: /Manifest/i });
    expect(manifest).toHaveAttribute("href", "/traces/trace_xyz/manifest");
    expect(manifest).toHaveAttribute("target", "_blank");
  });

  it("renders nothing while awaiting approval (that surface is the ApprovalCard)", () => {
    const { container } = render(<FinalCard done={done({ status: "pending_approval" })} />);
    expect(container).toBeEmptyDOMElement();
  });
});
