/**
 * Tests for PrimerPairsCard.
 *
 * Same content-first approach as CrisprReportCard's tests — verify the right
 * information surfaces, not styling. Numbers in the output (Tm, GC, product size)
 * are formatted by the component, so the tests pin the rendered strings.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PrimerPairsCard } from "../PrimerPairsCard";
import type { DesignPrimersOutput, PrimerPair } from "../../types/primers";

function makePair(overrides: Partial<PrimerPair> = {}): PrimerPair {
  return {
    rank: 0,
    forward_sequence: "GCAATTCCCAATGGCAAAGGT",
    forward_tm: 60.0,
    forward_gc_percent: 47.6,
    forward_start: 0,
    forward_length: 21,
    reverse_sequence: "ATTAAGCCACGTTCACCGGT",
    reverse_tm: 59.9,
    reverse_gc_percent: 50.0,
    reverse_start: 102,
    reverse_length: 20,
    product_size: 103,
    pair_penalty: 1.234,
    ...overrides,
  };
}

function makeOutput(overrides: Partial<DesignPrimersOutput> = {}): DesignPrimersOutput {
  return {
    template_length: 200,
    target_start: null,
    target_end: null,
    primer_pairs: [makePair()],
    num_returned: 1,
    primer3_warnings: [],
    caveats: ["primer3 does not verify specificity against a genome."],
    ...overrides,
  };
}

describe("PrimerPairsCard", () => {
  it("renders the header with template length and pair count", () => {
    render(<PrimerPairsCard output={makeOutput()} />);

    expect(screen.getByText(/PCR primer pairs/i)).toBeInTheDocument();
    const header = screen.getByText(/template 200 nt/);
    expect(header.textContent).toMatch(/1 pair$/);
  });

  it("includes target coordinates in the header when provided", () => {
    render(
      <PrimerPairsCard
        output={makeOutput({ target_start: 80, target_end: 130 })}
      />,
    );
    expect(screen.getByText(/target 80-130/)).toBeInTheDocument();
  });

  it("renders a primer pair with forward and reverse sequences + metrics", () => {
    render(<PrimerPairsCard output={makeOutput()} />);

    expect(screen.getByText("GCAATTCCCAATGGCAAAGGT")).toBeInTheDocument();
    expect(screen.getByText("ATTAAGCCACGTTCACCGGT")).toBeInTheDocument();
    // Each strand renders its own Tm + GC; component formats Tm to 1 decimal.
    expect(screen.getAllByText(/Tm 60\.0°C/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/GC 47\.6%/).length).toBeGreaterThanOrEqual(1);
    // Product size + penalty appear in the pair header.
    expect(screen.getByText(/103 bp/)).toBeInTheDocument();
    expect(screen.getByText(/penalty 1\.234/)).toBeInTheDocument();
  });

  it("shows the no-pairs message + primer3 warnings when num_returned is 0", () => {
    render(
      <PrimerPairsCard
        output={makeOutput({
          num_returned: 0,
          primer_pairs: [],
          primer3_warnings: [
            "PRIMER_LEFT_EXPLAIN: considered 200, no valid pair",
          ],
        })}
      />,
    );

    expect(screen.getByText(/No primer pairs found/i)).toBeInTheDocument();
    expect(screen.getByText(/considered 200/)).toBeInTheDocument();
  });

  it("renders the caveats panel", () => {
    render(<PrimerPairsCard output={makeOutput()} />);
    expect(screen.getByText(/^Caveats$/i)).toBeInTheDocument();
    expect(screen.getByText(/does not verify specificity/i)).toBeInTheDocument();
  });

  it("ranks pairs as #1, #2, etc.", () => {
    render(
      <PrimerPairsCard
        output={makeOutput({
          primer_pairs: [
            makePair({ rank: 0 }),
            makePair({ rank: 1, forward_sequence: "AAAAAAAAAAAAAAAAAAAA" }),
            makePair({ rank: 2, forward_sequence: "TTTTTTTTTTTTTTTTTTTT" }),
          ],
          num_returned: 3,
        })}
      />,
    );
    expect(screen.getByText(/Pair #1/)).toBeInTheDocument();
    expect(screen.getByText(/Pair #2/)).toBeInTheDocument();
    expect(screen.getByText(/Pair #3/)).toBeInTheDocument();
  });
});
