/**
 * GroundedResponse renders the final answer with each checked value highlighted inline:
 * a grounded value carries provenance in its tooltip; a flagged one carries a caution.
 * The component is honest by construction — an offset that doesn't line up with the
 * claim's surface text is skipped, never mis-highlighted.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { GroundedResponse } from "../GroundedResponse";
import type { EntityClaimVerdict, NumericClaimVerdict } from "../../types/agent";

function num(over: Partial<NumericClaimVerdict>): NumericClaimVerdict {
  return {
    text: "50%",
    value: 50,
    is_percent: true,
    start: 0,
    end: 3,
    status: "grounded",
    matched_path: null,
    matched_value: null,
    ...over,
  };
}

describe("GroundedResponse", () => {
  it("highlights a grounded numeric value with its provenance in the tooltip", () => {
    const text = "GC is 50% here";
    render(
      <GroundedResponse
        text={text}
        numericClaims={[
          num({ text: "50%", start: 6, end: 9, matched_path: "gc_content.gc_percent", matched_value: 50 }),
        ]}
      />,
    );
    const mark = screen.getByText("50%");
    expect(mark.tagName).toBe("MARK");
    expect(mark).toHaveAttribute("data-grounding", "grounded");
    expect(mark.getAttribute("title")).toMatch(/gc_content\.gc_percent = 50/);
    // The surrounding text is preserved.
    expect(screen.getByText(/GC is/)).toBeInTheDocument();
    expect(screen.getByText(/here/)).toBeInTheDocument();
  });

  it("flags an unsupported numeric value with a caution tooltip", () => {
    const text = "Affinity 0.92 nM";
    render(
      <GroundedResponse
        text={text}
        numericClaims={[
          num({ text: "0.92", value: 0.92, is_percent: false, start: 9, end: 13, status: "unsupported" }),
        ]}
      />,
    );
    const mark = screen.getByText("0.92");
    expect(mark).toHaveAttribute("data-grounding", "unsupported");
    expect(mark.getAttribute("title")).toMatch(/caution/i);
  });

  it("links a grounded identifier to its source database and names where it was found", () => {
    const text = "Top hit rs80357064 in ClinVar";
    const entity: EntityClaimVerdict = {
      text: "rs80357064",
      kind: "rsid",
      start: 8,
      end: 18,
      status: "grounded",
      matched_path: "fetch_clinvar.rsid",
    };
    render(<GroundedResponse text={text} entityClaims={[entity]} />);
    const link = screen.getByText("rs80357064");
    expect(link.tagName).toBe("A");
    expect(link).toHaveAttribute("href", "https://www.ncbi.nlm.nih.gov/snp/rs80357064");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link.getAttribute("title")).toMatch(/rsid found in fetch_clinvar\.rsid/);
    expect(link.getAttribute("title")).toMatch(/Opens dbSNP/);
  });

  it("links a PDB code to RCSB", () => {
    const text = "Structure 4HHB resolved at 1.74 A";
    render(
      <GroundedResponse
        text={text}
        entityClaims={[
          { text: "4HHB", kind: "pdb", start: 10, end: 14, status: "grounded", matched_path: "fetch_pdb.id" },
        ]}
      />,
    );
    const link = screen.getByText("4HHB");
    expect(link.tagName).toBe("A");
    expect(link).toHaveAttribute("href", "https://www.rcsb.org/structure/4HHB");
  });

  it("renders an unrecognized identifier kind as a non-link highlight", () => {
    const text = "code XYZ123 here";
    render(
      <GroundedResponse
        text={text}
        entityClaims={[
          { text: "XYZ123", kind: "weird", start: 5, end: 11, status: "grounded", matched_path: "x" },
        ]}
      />,
    );
    const el = screen.getByText("XYZ123");
    expect(el.tagName).toBe("MARK");
  });

  it("skips a span whose offsets don't match its surface text (no mis-highlight)", () => {
    const text = "value is 50%";
    // Claim says "99%" but offsets 9..12 cover "50%" — mismatch, must be skipped.
    render(
      <GroundedResponse
        text={text}
        numericClaims={[num({ text: "99%", start: 9, end: 12, status: "grounded" })]}
      />,
    );
    expect(screen.queryByText("99%")).not.toBeInTheDocument();
    expect(document.querySelector("mark")).toBeNull();
    // The full answer is still rendered intact.
    expect(screen.getByText(/value is 50%/)).toBeInTheDocument();
  });

  it("renders plain text unchanged when there are no claims", () => {
    render(<GroundedResponse text="just words" />);
    expect(screen.getByText("just words")).toBeInTheDocument();
    expect(document.querySelector("mark")).toBeNull();
  });
});
