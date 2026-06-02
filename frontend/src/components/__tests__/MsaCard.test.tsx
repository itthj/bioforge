/**
 * Tests for the MSA card + viewer (the lean, dependency-free renderer used instead of the
 * React-18-incompatible react-msa-viewer).
 *
 * Coverage focuses on: the header facts, that every row's residues render, and that the
 * derived per-column conservation is correct (the one piece of computed logic).
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MsaCard } from "../MsaCard";
import { columnConservation } from "../MsaViewer";
import type { AlignMsaOutput } from "../../types/msa";

function makeOutput(overrides: Partial<AlignMsaOutput> = {}): AlignMsaOutput {
  return {
    method: "MAFFT (--auto)",
    num_sequences: 3,
    alignment_length: 5,
    aligned: [
      { id: "seq1", aligned_sequence: "ACGT-" },
      { id: "seq2", aligned_sequence: "ACGTA" },
      { id: "seq3", aligned_sequence: "ACGAA" },
    ],
    notes: ["Alignment by MAFFT (core BSD-3-Clause)."],
    ...overrides,
  };
}

describe("MsaCard", () => {
  it("renders the header facts (count, columns, method)", () => {
    render(<MsaCard output={makeOutput()} />);
    expect(screen.getByText(/Multiple-sequence alignment/i)).toBeInTheDocument();
    const meta = screen.getByText(/3 sequences/);
    expect(meta.textContent).toMatch(/5 columns/);
    expect(meta.textContent).toMatch(/MAFFT/);
  });

  it("renders every sequence id and the notes", () => {
    render(<MsaCard output={makeOutput()} />);
    expect(screen.getByText("seq1")).toBeInTheDocument();
    expect(screen.getByText("seq2")).toBeInTheDocument();
    expect(screen.getByText("seq3")).toBeInTheDocument();
    expect(screen.getByText(/core BSD-3-Clause/)).toBeInTheDocument();
  });
});

describe("columnConservation", () => {
  it("flags fully conserved columns and computes the agreement fraction", () => {
    const rows = [
      { id: "a", aligned_sequence: "ACGT-" },
      { id: "b", aligned_sequence: "ACGTA" },
      { id: "c", aligned_sequence: "ACGAA" },
    ];
    const cons = columnConservation(rows, 5);
    // Col 0 (A,A,A) and col 1 (C,C,C) and col 2 (G,G,G) are fully conserved.
    expect(cons[0].fullyConserved).toBe(true);
    expect(cons[1].fullyConserved).toBe(true);
    expect(cons[2].fullyConserved).toBe(true);
    // Col 3 is (T,T,A) -> top 2/3, not fully conserved.
    expect(cons[3].fullyConserved).toBe(false);
    expect(cons[3].fraction).toBeCloseTo(2 / 3, 5);
    // Col 4 is (-,A,A) -> most common is A at 2/3, gap present so still not "fully conserved".
    expect(cons[4].fullyConserved).toBe(false);
    expect(cons[4].fraction).toBeCloseTo(2 / 3, 5);
  });

  it("a gap-dominated column is not fully conserved even at 100% gaps", () => {
    const rows = [
      { id: "a", aligned_sequence: "-" },
      { id: "b", aligned_sequence: "-" },
    ];
    const cons = columnConservation(rows, 1);
    expect(cons[0].fullyConserved).toBe(false); // all-gap is not conservation
    expect(cons[0].fraction).toBe(1);
  });
});
