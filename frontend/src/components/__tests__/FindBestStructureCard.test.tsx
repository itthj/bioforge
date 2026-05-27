/**
 * Tests for FindBestStructureCard.
 *
 * The component delegates the actual structure rendering to PdbStructureCard
 * or StructureCard depending on result.source. We assert:
 *   - The decision banner renders with the right source label + reason
 *   - Composite-level caveats render
 *   - Candidate alternatives table appears when len > 1
 *   - The right child card is rendered (presence of a unique marker)
 *   - Defensive fallback fires when the embedded child is missing
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FindBestStructureCard } from "../FindBestStructureCard";
import type { FindBestStructureOutput } from "../../types/find_best_structure";
import type { FetchPdbOutput } from "../../types/pdb_structure";
import type { FetchAlphaFoldOutput } from "../../types/structure";

function fakePdb(): FetchPdbOutput {
  return {
    pdb_id: "1MX6",
    title: "BRCA1 BRCT DOMAIN",
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
    caveats: ["Crystal contacts can shift loop conformations."],
  };
}

function fakeAlphaFold(): FetchAlphaFoldOutput {
  return {
    uniprot_id: "Q9NRP7",
    entry_id: "AF-Q9NRP7-F1",
    organism: "Homo sapiens",
    gene: "STK32C",
    uniprot_description: "Serine/threonine-protein kinase 32C",
    length_residues: 50,
    average_plddt: 65.0,
    plddt_distribution: {
      very_high: 5,
      confident: 15,
      low: 20,
      very_low: 10,
    },
    pdb_url: "https://alphafold.ebi.ac.uk/files/AF-Q9NRP7-F1-model_v4.pdb",
    cif_url: "https://alphafold.ebi.ac.uk/files/AF-Q9NRP7-F1-model_v4.cif",
    pae_image_url: null,
    latest_version: 4,
    model_created_date: "2022-11-01",
    pdb_text: "HEADER\nEND\n",
    caveats: ["AlphaFold predictions are computational, not experimental."],
  };
}

function makeExperimental(
  overrides: Partial<FindBestStructureOutput> = {},
): FindBestStructureOutput {
  return {
    uniprot_id: "P38398",
    source: "experimental",
    reason: "SIFTS top match for UniProt P38398 is PDB 1MX6 (X-ray, 1.85 Å, covering 12% of the sequence).",
    experimental_candidates: [
      {
        pdb_id: "1MX6",
        chain_id: "A",
        coverage: 0.12,
        resolution_angstrom: 1.85,
        experimental_method: "X-ray diffraction",
        unp_start: 1646,
        unp_end: 1863,
      },
    ],
    pdb_result: fakePdb(),
    alphafold_result: null,
    caveats: ["The chosen experimental structure covers only 12% of the full UniProt sequence."],
    ...overrides,
  };
}

function makePredicted(
  overrides: Partial<FindBestStructureOutput> = {},
): FindBestStructureOutput {
  return {
    uniprot_id: "Q9NRP7",
    source: "predicted",
    reason: "No experimental structure mapped to UniProt Q9NRP7 in SIFTS. Falling back to the AlphaFold prediction.",
    experimental_candidates: [],
    pdb_result: null,
    alphafold_result: fakeAlphaFold(),
    caveats: ["No experimental coverage was found via SIFTS."],
    ...overrides,
  };
}

describe("FindBestStructureCard", () => {
  it("shows experimental banner + reason when source=experimental", () => {
    render(<FindBestStructureCard result={makeExperimental()} />);
    expect(screen.getByText(/Experimental structure chosen/i)).toBeInTheDocument();
    expect(screen.getByText(/SIFTS top match/)).toBeInTheDocument();
    // UniProt accession on the right side of the banner.
    expect(screen.getByText("P38398")).toBeInTheDocument();
  });

  it("shows predicted banner + reason when source=predicted", () => {
    render(<FindBestStructureCard result={makePredicted()} />);
    expect(screen.getByText(/Predicted structure chosen/i)).toBeInTheDocument();
    expect(screen.getByText(/Falling back to the AlphaFold prediction/)).toBeInTheDocument();
  });

  it("renders composite-level caveats", () => {
    render(<FindBestStructureCard result={makeExperimental()} />);
    expect(screen.getByText(/Decision caveats \(1\)/)).toBeInTheDocument();
    expect(
      screen.getByText(/covers only 12% of the full UniProt sequence/),
    ).toBeInTheDocument();
  });

  it("embeds PdbStructureCard for experimental results", () => {
    render(<FindBestStructureCard result={makeExperimental()} />);
    // Marker unique to PdbStructureCard: the link to RCSB structure page.
    expect(
      screen.getByRole("link", { name: "1MX6" }),
    ).toBeInTheDocument();
    // PdbStructureCard's caveat label.
    expect(screen.getByText(/Interpretation caveats/)).toBeInTheDocument();
  });

  it("embeds StructureCard (AlphaFold) for predicted results", () => {
    render(<FindBestStructureCard result={makePredicted()} />);
    // Marker unique to StructureCard: pLDDT confidence distribution heading.
    expect(screen.getByText(/pLDDT confidence distribution/i)).toBeInTheDocument();
    // The AlphaFold caveat from the embedded child.
    expect(
      screen.getByText(/AlphaFold predictions are computational/),
    ).toBeInTheDocument();
  });

  it("shows candidate alternatives table when multiple candidates exist", () => {
    render(
      <FindBestStructureCard
        result={makeExperimental({
          experimental_candidates: [
            {
              pdb_id: "1ABC",
              chain_id: "A",
              coverage: 0.95,
              resolution_angstrom: 1.5,
              experimental_method: "X-ray diffraction",
              unp_start: 1,
              unp_end: 200,
            },
            {
              pdb_id: "2DEF",
              chain_id: "B",
              coverage: 0.5,
              resolution_angstrom: 1.0,
              experimental_method: "X-ray diffraction",
              unp_start: 1,
              unp_end: 100,
            },
          ],
        })}
      />,
    );
    expect(
      screen.getByText(/Alternative experimental candidates \(2\)/),
    ).toBeInTheDocument();
    expect(screen.getByText("95%")).toBeInTheDocument();
    expect(screen.getByText("50%")).toBeInTheDocument();
  });

  it("does NOT show alternatives table for single-candidate or zero-candidate results", () => {
    // 1 candidate.
    const { rerender } = render(<FindBestStructureCard result={makeExperimental()} />);
    expect(
      screen.queryByText(/Alternative experimental candidates/),
    ).not.toBeInTheDocument();
    // 0 candidates (predicted fallback).
    rerender(<FindBestStructureCard result={makePredicted()} />);
    expect(
      screen.queryByText(/Alternative experimental candidates/),
    ).not.toBeInTheDocument();
  });

  it("shows defensive fallback if backend says experimental but pdb_result is missing", () => {
    render(
      <FindBestStructureCard
        result={makeExperimental({ pdb_result: null })}
      />,
    );
    expect(
      screen.getByText(/source='experimental' but no pdb_result/),
    ).toBeInTheDocument();
  });

  it("shows defensive fallback if backend says predicted but alphafold_result is missing", () => {
    render(
      <FindBestStructureCard
        result={makePredicted({ alphafold_result: null })}
      />,
    );
    expect(
      screen.getByText(/source='predicted' but no alphafold_result/),
    ).toBeInTheDocument();
  });
});
