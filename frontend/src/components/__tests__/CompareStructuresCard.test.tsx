import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CompareStructuresCard } from "../CompareStructuresCard";
import type { CompareStructuresOutput } from "../../types/compare_structures";
import type { FetchPdbOutput } from "../../types/pdb_structure";
import type { FetchAlphaFoldOutput } from "../../types/structure";

function fakePdb(): FetchPdbOutput {
  return {
    pdb_id: "1MX6",
    title: "BRCA1 BRCT",
    experimental_method: "X-RAY DIFFRACTION",
    resolution_angstrom: 1.85,
    deposit_date: "2002-01-01",
    release_date: "2002-06-01",
    revision_date: "2024-10-30",
    keywords: "DNA BINDING PROTEIN",
    chain_ids: ["A"],
    num_chains: 1,
    num_residues: 218,
    residues_per_chain: { A: 218 },
    ligand_ids: [],
    mean_b_factor: 22.1,
    pdb_url: "https://files.rcsb.org/download/1MX6.pdb",
    cif_url: "https://files.rcsb.org/download/1MX6.cif",
    pdb_text: "HEADER\nEND\n",
    caveats: [],
  };
}

function fakeAf(): FetchAlphaFoldOutput {
  return {
    uniprot_id: "P38398",
    entry_id: "AF-P38398-F1",
    organism: "Homo sapiens",
    gene: "BRCA1",
    uniprot_description: "Breast cancer type 1",
    length_residues: 1863,
    average_plddt: 65.0,
    plddt_distribution: { very_high: 200, confident: 500, low: 800, very_low: 363 },
    pdb_url: "https://alphafold.ebi.ac.uk/files/AF-P38398-F1-model_v4.pdb",
    cif_url: "https://alphafold.ebi.ac.uk/files/AF-P38398-F1-model_v4.cif",
    pae_image_url: null,
    latest_version: 4,
    model_created_date: "2022-11-01",
    pdb_text: "HEADER\nEND\n",
    caveats: [],
  };
}

function makeResult(
  overrides: Partial<CompareStructuresOutput> = {},
): CompareStructuresOutput {
  return {
    uniprot_id: "P38398",
    experimental: fakePdb(),
    predicted: fakeAf(),
    overlap: {
      experimental_start: 1646,
      experimental_end: 1863,
      alphafold_length: 1863,
      overlap_start: 1646,
      overlap_end: 1863,
      overlap_residues: 218,
      experimental_only_residues: 0,
      predicted_only_residues: 1645,
    },
    summary:
      "Experimental structure: PDB 1MX6 (X-RAY DIFFRACTION, resolution 1.85 Å). " +
      "AlphaFold prediction: AF-P38398-F1, mean pLDDT 65.0.",
    caveats: ["Per-residue RMSD is not computed by this version."],
    ...overrides,
  };
}

describe("CompareStructuresCard", () => {
  it("renders the banner with summary + UniProt accession", () => {
    render(<CompareStructuresCard result={makeResult()} />);
    expect(screen.getByText(/Structure comparison/i)).toBeInTheDocument();
    // UniProt ID appears in the banner + embedded AlphaFold card.
    expect(screen.getAllByText("P38398").length).toBeGreaterThan(0);
    expect(screen.getByText(/PDB 1MX6/)).toBeInTheDocument();
    // AF entry ID appears in banner + StructureCard child header.
    expect(screen.getAllByText(/AF-P38398-F1/).length).toBeGreaterThan(0);
  });

  it("renders coverage legend with overlap + prediction-only counts", () => {
    render(<CompareStructuresCard result={makeResult()} />);
    expect(screen.getByText(/Validated overlap: 218 aa/)).toBeInTheDocument();
    expect(screen.getByText(/Prediction-only: 1645 aa/)).toBeInTheDocument();
    // Experimental-only is zero here, so its legend item is hidden.
    expect(screen.queryByText(/Experimental-only/)).not.toBeInTheDocument();
  });

  it("renders experimental-only chip when isoform mismatch", () => {
    render(
      <CompareStructuresCard
        result={makeResult({
          overlap: {
            experimental_start: 1,
            experimental_end: 100,
            alphafold_length: 50,
            overlap_start: null,
            overlap_end: null,
            overlap_residues: 0,
            experimental_only_residues: 100,
            predicted_only_residues: 50,
          },
        })}
      />,
    );
    expect(screen.getByText(/Experimental-only: 100 aa/)).toBeInTheDocument();
  });

  it("starts on side-by-side tab and renders both cards", () => {
    render(<CompareStructuresCard result={makeResult()} />);
    // Both child cards present: PdbStructureCard link to RCSB + StructureCard pLDDT bar.
    expect(screen.getByRole("link", { name: "1MX6" })).toBeInTheDocument();
    expect(screen.getByText(/pLDDT confidence distribution/i)).toBeInTheDocument();
  });

  it("switching to predicted tab hides experimental card", () => {
    render(<CompareStructuresCard result={makeResult()} />);
    const predictedTab = screen.getByRole("button", { name: /Predicted/ });
    fireEvent.click(predictedTab);
    // Experimental card link is gone — only the predicted card remains.
    expect(screen.queryByRole("link", { name: "1MX6" })).not.toBeInTheDocument();
    expect(screen.getByText(/pLDDT confidence distribution/i)).toBeInTheDocument();
  });

  it("switching to experimental tab hides AlphaFold card", () => {
    render(<CompareStructuresCard result={makeResult()} />);
    const experimentalTab = screen.getByRole("button", { name: /Experimental/ });
    fireEvent.click(experimentalTab);
    expect(
      screen.queryByText(/pLDDT confidence distribution/i),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "1MX6" })).toBeInTheDocument();
  });

  it("renders comparison-level caveats", () => {
    render(<CompareStructuresCard result={makeResult()} />);
    expect(screen.getByText(/Per-residue RMSD is not computed/)).toBeInTheDocument();
  });
});
