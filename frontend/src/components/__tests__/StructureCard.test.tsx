/**
 * Tests for StructureCard.
 *
 * Mol* is dynamically imported on first "Load Mol* viewer" click — we don't try
 * to assert anything about WebGL rendering inside happy-dom (it's headless and
 * Mol* is ~4 MB). Instead the tests verify everything *around* the 3D viewer:
 * the metadata header, the pLDDT bar, the caveats panel, the missing-PDB
 * fallback, and the "load viewer" button state machine.
 */

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StructureCard } from "../StructureCard";
import type { FetchAlphaFoldOutput } from "../../types/structure";

function makeStructure(overrides: Partial<FetchAlphaFoldOutput> = {}): FetchAlphaFoldOutput {
  return {
    uniprot_id: "P38398",
    entry_id: "AF-P38398-F1",
    organism: "Homo sapiens",
    gene: "BRCA1",
    uniprot_description: "Breast cancer type 1 susceptibility protein",
    length_residues: 100,
    average_plddt: 78.4,
    plddt_distribution: {
      very_high: 40,
      confident: 30,
      low: 20,
      very_low: 10,
    },
    pdb_url: "https://alphafold.ebi.ac.uk/files/AF-P38398-F1-model_v4.pdb",
    cif_url: "https://alphafold.ebi.ac.uk/files/AF-P38398-F1-model_v4.cif",
    pae_image_url: null,
    latest_version: 4,
    model_created_date: "2022-11-01",
    pdb_text: "HEADER PREDICTED MODEL\nATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 80.00           C\nEND\n",
    caveats: [
      "AlphaFold predictions are computational, not experimental.",
      "Single-chain predictions miss multimer interface effects.",
    ],
    ...overrides,
  };
}

describe("StructureCard", () => {
  it("renders gene, UniProt accession, organism, and entry ID", () => {
    render(<StructureCard structure={makeStructure()} />);
    expect(screen.getByText("BRCA1")).toBeInTheDocument();
    expect(screen.getByText("P38398")).toBeInTheDocument();
    expect(screen.getByText(/Homo sapiens/)).toBeInTheDocument();
    expect(screen.getByText("AF-P38398-F1")).toBeInTheDocument();
  });

  it("shows the average pLDDT prominently with 1-decimal precision", () => {
    render(<StructureCard structure={makeStructure({ average_plddt: 87.231 })} />);
    expect(screen.getByText("87.2")).toBeInTheDocument();
    expect(screen.getByText(/avg pLDDT/i)).toBeInTheDocument();
  });

  it("shows the residue count", () => {
    render(<StructureCard structure={makeStructure({ length_residues: 1863 })} />);
    expect(screen.getByText(/1863 residues/)).toBeInTheDocument();
  });

  it("renders each pLDDT bin label with its residue count", () => {
    render(<StructureCard structure={makeStructure()} />);
    expect(screen.getByText(/Very high \(≥90\)/)).toBeInTheDocument();
    expect(screen.getByText(/Confident \(70-89\)/)).toBeInTheDocument();
    expect(screen.getByText(/Low \(50-69\)/)).toBeInTheDocument();
    expect(screen.getByText(/Very low \(<50\)/)).toBeInTheDocument();
    // Counts are formatted as plain numbers.
    expect(screen.getByText("40")).toBeInTheDocument();
    expect(screen.getByText("30")).toBeInTheDocument();
  });

  it("renders the mandatory caveats list, expanded by default", () => {
    render(<StructureCard structure={makeStructure()} />);
    // Caveats summary visible.
    expect(screen.getByText(/Prediction caveats \(2\)/)).toBeInTheDocument();
    // Both caveat strings visible (the <details> is open=true).
    expect(
      screen.getByText(/AlphaFold predictions are computational/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Single-chain predictions miss multimer/),
    ).toBeInTheDocument();
  });

  it("shows the load-Mol*-viewer button when PDB text is present", () => {
    render(<StructureCard structure={makeStructure()} />);
    expect(
      screen.getByRole("button", { name: /Load Mol\* viewer/i }),
    ).toBeInTheDocument();
  });

  it("transitions to loading state when the load button is clicked", async () => {
    render(<StructureCard structure={makeStructure()} />);
    const btn = screen.getByRole("button", { name: /Load Mol\* viewer/i });
    fireEvent.click(btn);
    // Either we see the loading state momentarily, or the error state (Mol*
    // is not installed in this test environment). Both are acceptable — what
    // matters is that the button isn't still showing.
    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: /Load Mol\* viewer/i }),
      ).not.toBeInTheDocument();
    });
  });

  it("falls back to a download link when no PDB text is in the response", () => {
    render(<StructureCard structure={makeStructure({ pdb_text: null })} />);
    expect(screen.getByText(/No PDB text in this response/)).toBeInTheDocument();
    const link = screen.getByRole("link");
    expect(link).toHaveAttribute(
      "href",
      "https://alphafold.ebi.ac.uk/files/AF-P38398-F1-model_v4.pdb",
    );
  });

  it("shows the raw PDB text in a collapsible details element", () => {
    render(<StructureCard structure={makeStructure()} />);
    // Title includes size in KB.
    expect(screen.getByText(/Raw PDB text/)).toBeInTheDocument();
  });

  it("handles missing gene + organism gracefully", () => {
    render(
      <StructureCard
        structure={makeStructure({ gene: null, organism: null })}
      />,
    );
    expect(screen.getByText("Unknown gene")).toBeInTheDocument();
    expect(screen.getByText(/Unknown organism/)).toBeInTheDocument();
  });
});
