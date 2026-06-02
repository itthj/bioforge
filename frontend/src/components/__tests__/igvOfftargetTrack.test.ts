/**
 * Tests for the pure off-target hg38 adapter (Slice B).
 *
 * The integrity-critical behavior is the placeable/non-placeable split and that only
 * placed hits become features — verified thoroughly here so the component stays a thin
 * lazy-load shell.
 */

import { describe, expect, it } from "vitest";
import {
  IGV_HG38_GENOME,
  buildOfftargetFeatures,
  buildOfftargetIgvConfig,
  buildOfftargetLocus,
  splitByPlaceability,
} from "../igvOfftargetTrack";
import type { GenomicPlacement, OfftargetHit, RiskLabel } from "../../types/crispr";

function placement(over: Partial<GenomicPlacement> = {}): GenomicPlacement {
  return {
    build: "GRCh38",
    chromosome: "chr1",
    start: 1000,
    end: 1020,
    strand: "+",
    source_accession: "NC_000001.11",
    ...over,
  };
}

function hit(over: Partial<OfftargetHit> = {}): OfftargetHit {
  return {
    accession: "NC_000001.11",
    organism: "Homo sapiens",
    subject_definition: "chr1",
    mismatch_count: 2,
    mit_score: 0.5,
    cfd_mismatch_score: 0.4,
    risk_label: "high" as RiskLabel,
    genomic_placement: placement(),
    genomic_placement_note: null,
    ...over,
  };
}

describe("splitByPlaceability", () => {
  it("separates hits with a genomic_placement from those without", () => {
    const placed = hit();
    const unplaced = hit({ accession: "NM_007294.4", genomic_placement: null });
    const { placeable, nonPlaceable } = splitByPlaceability([placed, unplaced]);
    expect(placeable).toEqual([placed]);
    expect(nonPlaceable).toEqual([unplaced]);
  });
});

describe("buildOfftargetFeatures", () => {
  it("emits one feature per placeable hit, passing placement coords through", () => {
    const features = buildOfftargetFeatures([
      hit({ genomic_placement: placement({ chromosome: "chrX", start: 50, end: 70, strand: "-" }) }),
    ]);
    expect(features).toHaveLength(1);
    expect(features[0]).toMatchObject({ chr: "chrX", start: 50, end: 70, strand: "-" });
    expect(features[0].name).toContain("2mm");
  });

  it("excludes non-placeable hits entirely (never a guessed locus)", () => {
    const features = buildOfftargetFeatures([hit({ genomic_placement: null })]);
    expect(features).toHaveLength(0);
  });

  it("colors features by risk", () => {
    const [high] = buildOfftargetFeatures([hit({ risk_label: "high" })]);
    const [low] = buildOfftargetFeatures([hit({ risk_label: "low" })]);
    expect(high.color).not.toBe(low.color);
  });
});

describe("buildOfftargetLocus", () => {
  it("focuses the highest-risk placeable hit, padded, as a 1-based locus", () => {
    const locus = buildOfftargetLocus([
      hit({ risk_label: "low", genomic_placement: placement({ chromosome: "chr2", start: 5000, end: 5020 }) }),
      hit({ risk_label: "high", genomic_placement: placement({ chromosome: "chr7", start: 1000, end: 1020 }) }),
    ]);
    // chr7 (high risk) wins; start 1000 (0-based) -> 1001 (1-based) - 60 padding = 941; end 1020 + 60 = 1080.
    expect(locus).toBe("chr7:941-1080");
  });

  it("clamps the locus start at 1 and returns null when nothing is placeable", () => {
    expect(buildOfftargetLocus([hit({ genomic_placement: placement({ start: 5, end: 25 }) })])).toBe(
      "chr1:1-85",
    );
    expect(buildOfftargetLocus([hit({ genomic_placement: null })])).toBeNull();
  });
});

describe("buildOfftargetIgvConfig", () => {
  it("targets the hosted hg38 genome with one annotation track", () => {
    const config = buildOfftargetIgvConfig([hit()]);
    expect(config.genome).toBe(IGV_HG38_GENOME);
    expect(config.genome).toBe("hg38");
    expect(config.tracks).toHaveLength(1);
    expect((config.tracks[0].features as unknown[]).length).toBe(1);
  });
});
