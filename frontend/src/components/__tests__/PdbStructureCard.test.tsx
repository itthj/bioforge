/**
 * Tests for PdbStructureCard.
 *
 * Same approach as StructureCard's tests — don't try to render Mol* in
 * happy-dom; verify the metadata header, chain pills, ligand pills, caveats,
 * and viewer state machine fallbacks.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PdbStructureCard } from "../PdbStructureCard";
import type { FetchPdbOutput } from "../../types/pdb_structure";

function makeStructure(overrides: Partial<FetchPdbOutput> = {}): FetchPdbOutput {
  return {
    pdb_id: "4HHB",
    title: "STRUCTURE OF HUMAN DEOXYHAEMOGLOBIN",
    experimental_method: "X-RAY DIFFRACTION",
    resolution_angstrom: 1.74,
    deposit_date: "1984-03-07",
    release_date: "1984-07-17",
    revision_date: "2024-10-30",
    keywords: "OXYGEN TRANSPORT, HEMOGLOBIN",
    chain_ids: ["A", "B", "C", "D"],
    num_chains: 4,
    num_residues: 574,
    residues_per_chain: { A: 141, B: 146, C: 141, D: 146 },
    ligand_ids: ["HEM", "FE"],
    mean_b_factor: 24.3,
    pdb_url: "https://files.rcsb.org/download/4HHB.pdb",
    cif_url: "https://files.rcsb.org/download/4HHB.cif",
    pdb_text: "HEADER TEST\nATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 24.30           C\nEND\n",
    caveats: [
      "Experimental structures capture one snapshot of conformational space.",
      "Crystal contacts can shift loop conformations.",
    ],
    ...overrides,
  };
}

describe("PdbStructureCard", () => {
  it("renders PDB ID as a link to RCSB and shows the title", () => {
    render(<PdbStructureCard structure={makeStructure()} />);
    const link = screen.getByRole("link", { name: "4HHB" });
    expect(link).toHaveAttribute("href", "https://www.rcsb.org/structure/4HHB");
    expect(screen.getByText(/STRUCTURE OF HUMAN DEOXYHAEMOGLOBIN/)).toBeInTheDocument();
  });

  it("shows experimental method, resolution, and release date", () => {
    render(<PdbStructureCard structure={makeStructure()} />);
    expect(screen.getByText(/X-RAY DIFFRACTION/)).toBeInTheDocument();
    expect(screen.getByText(/1\.74 Å/)).toBeInTheDocument();
    expect(screen.getByText(/released 1984-07-17/)).toBeInTheDocument();
  });

  it("renders one pill per chain with residue count", () => {
    render(<PdbStructureCard structure={makeStructure()} />);
    // Chain labels.
    expect(screen.getByText("A")).toBeInTheDocument();
    expect(screen.getByText("B")).toBeInTheDocument();
    expect(screen.getByText("C")).toBeInTheDocument();
    expect(screen.getByText("D")).toBeInTheDocument();
    // Residue counts.
    expect(screen.getAllByText(/141aa/).length).toBe(2); // A and C
    expect(screen.getAllByText(/146aa/).length).toBe(2); // B and D
  });

  it("renders ligand pills with links to RCSB ligand catalog", () => {
    render(<PdbStructureCard structure={makeStructure()} />);
    const hemLink = screen.getByRole("link", { name: "HEM" });
    expect(hemLink).toHaveAttribute("href", "https://www.rcsb.org/ligand/HEM");
    expect(screen.getByText(/Ligands \/ cofactors \(2\)/)).toBeInTheDocument();
  });

  it("renders caveats expanded by default", () => {
    render(<PdbStructureCard structure={makeStructure()} />);
    expect(screen.getByText(/Interpretation caveats \(2\)/)).toBeInTheDocument();
    expect(
      screen.getByText(/Experimental structures capture one snapshot/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Crystal contacts can shift loop/),
    ).toBeInTheDocument();
  });

  it("shows mean B-factor when present", () => {
    render(<PdbStructureCard structure={makeStructure({ mean_b_factor: 18.5 })} />);
    expect(screen.getByText(/⟨B⟩ 18\.5 Å²/)).toBeInTheDocument();
  });

  it("omits B-factor row when null (e.g. NMR)", () => {
    render(<PdbStructureCard structure={makeStructure({ mean_b_factor: null })} />);
    expect(screen.queryByText(/⟨B⟩/)).not.toBeInTheDocument();
  });

  it("renders load-Mol* button when pdb_text is present", () => {
    render(<PdbStructureCard structure={makeStructure()} />);
    expect(
      screen.getByRole("button", { name: /Load Mol\* viewer/i }),
    ).toBeInTheDocument();
  });

  it("falls back to a download link when pdb_text is null", () => {
    render(<PdbStructureCard structure={makeStructure({ pdb_text: null })} />);
    expect(screen.getByText(/No PDB text in this response/)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /files\.rcsb\.org/ }),
    ).toBeInTheDocument();
  });

  it("renders raw PDB collapsible with size label", () => {
    render(<PdbStructureCard structure={makeStructure()} />);
    expect(screen.getByText(/Raw PDB text/)).toBeInTheDocument();
  });

  it("hides chain pill section when no chains", () => {
    render(
      <PdbStructureCard
        structure={makeStructure({
          chain_ids: [],
          num_chains: 0,
          residues_per_chain: {},
          num_residues: 0,
        })}
      />,
    );
    // The uppercase tracking-wide section label only appears with chain pills.
    // The header summary's "chains" text (lowercase) is always present.
    expect(screen.queryByText("Chains")).not.toBeInTheDocument();
    expect(screen.queryByText(/aa$/)).not.toBeInTheDocument();
  });
});
