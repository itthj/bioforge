import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { InterproCard } from "../InterproCard";
import type { FetchInterproOutput } from "../../types/interpro";

function makeOutput(overrides: Partial<FetchInterproOutput> = {}): FetchInterproOutput {
  return {
    uniprot_id: "P38398",
    num_entries: 2,
    domains: [
      {
        interpro_id: "IPR001357",
        name: "BRCT",
        type: "domain",
        regions: [
          { start: 1646, end: 1736 },
          { start: 1760, end: 1855 },
        ],
      },
      {
        interpro_id: "IPR025202",
        name: "BRCA1 zinc finger",
        type: "active_site",
        regions: [{ start: 24, end: 64 }],
      },
    ],
    caveats: ["InterPro entries are predicted, not experimental."],
    ...overrides,
  };
}

describe("InterproCard", () => {
  it("renders domain rows with names, IDs as RCSB links, and region ranges", () => {
    render(<InterproCard output={makeOutput()} />);
    expect(screen.getByText("BRCT")).toBeInTheDocument();
    expect(screen.getByText("BRCA1 zinc finger")).toBeInTheDocument();

    const brctLink = screen.getByRole("link", { name: "IPR001357" });
    expect(brctLink).toHaveAttribute(
      "href",
      "https://www.ebi.ac.uk/interpro/entry/InterPro/IPR001357/",
    );
    expect(screen.getByText(/1646-1736, 1760-1855/)).toBeInTheDocument();
    expect(screen.getByText(/24-64/)).toBeInTheDocument();
  });

  it("shows the type legend with one swatch per type used", () => {
    render(<InterproCard output={makeOutput()} />);
    expect(screen.getByText("Domain")).toBeInTheDocument();
    expect(screen.getByText("Active site")).toBeInTheDocument();
  });

  it("renders ProtVista-lite domain tracks when proteinLength is supplied", () => {
    const { container } = render(
      <InterproCard output={makeOutput()} proteinLength={1863} />,
    );
    // Each region within each domain gets a positioned absolute div with bg color.
    // BRCT has 2 regions, zinc finger has 1 → expect 3 colored bars.
    const bars = container.querySelectorAll(
      'div.absolute[style*="left"]',
    );
    expect(bars.length).toBe(3);
  });

  it("does NOT render tracks when proteinLength is missing", () => {
    const { container } = render(<InterproCard output={makeOutput()} />);
    const bars = container.querySelectorAll(
      'div.absolute[style*="left"]',
    );
    expect(bars.length).toBe(0);
  });

  it("renders the caveats", () => {
    render(<InterproCard output={makeOutput()} />);
    expect(screen.getByText(/predicted, not experimental/)).toBeInTheDocument();
  });

  it("handles an empty domain list gracefully", () => {
    render(
      <InterproCard
        output={makeOutput({ domains: [], num_entries: 0, caveats: ["No InterPro entries returned."] })}
      />,
    );
    expect(screen.getByText("0 entries")).toBeInTheDocument();
    expect(screen.getByText(/No InterPro entries returned/)).toBeInTheDocument();
  });
});
