/**
 * Tests for the pure igv.js guide-track adapter.
 *
 * This is where the coordinate + color logic lives, so it gets the thorough
 * coverage; the React component is a thin lazy-load shell on top of it.
 */

import { describe, expect, it } from "vitest";
import {
  TARGET_CONTIG,
  buildGuideFeatures,
  buildIgvConfig,
  buildTargetFasta,
  guideLocus,
} from "../igvGuideTrack";
import type { CrisprEditReportOutput, GuideReport } from "../../types/crispr";

function makeGuide(overrides: Partial<GuideReport> = {}): GuideReport {
  return {
    rank: 1,
    protospacer: "ACGTACGTACGTACGTACGG",
    pam_sequence: "AGG",
    strand: "+",
    protospacer_start: 10,
    protospacer_end: 30,
    pam_start: 30,
    pam_end: 33,
    heuristic_score: 0.8,
    on_target_score: 0.7,
    recommendation_score: 0.75,
    recommendation_label: "preferred",
    rationale: [],
    off_target_summary: {
      searched: false,
      database: null,
      high_risk_count: 0,
      medium_risk_count: 0,
      low_risk_count: 0,
      top_hits: [],
      caveats: [],
    },
    edit_outcome_summary: null,
    ...overrides,
  };
}

function makeReport(guides: GuideReport[]): CrisprEditReportOutput {
  return {
    target_length: 60,
    target_sequence: "A".repeat(60),
    pam: "NGG",
    num_guides_considered: guides.length,
    recommended_guide: guides[0] ?? null,
    guides,
    tool_chain: ["design_guides"],
    caveats: [],
  };
}

describe("buildTargetFasta", () => {
  it("emits a single-contig FASTA with the contig header", () => {
    expect(buildTargetFasta("ACGTACGT")).toBe(">target\nACGTACGT\n");
  });

  it("wraps long sequences at 60 columns", () => {
    const seq = "A".repeat(130);
    const fasta = buildTargetFasta(seq);
    const lines = fasta.trimEnd().split("\n");
    expect(lines[0]).toBe(">target");
    expect(lines[1]).toHaveLength(60);
    expect(lines[2]).toHaveLength(60);
    expect(lines[3]).toHaveLength(10);
    // Round-trips to the original sequence.
    expect(lines.slice(1).join("")).toBe(seq);
  });

  it("honors a custom contig name", () => {
    expect(buildTargetFasta("ACGT", "locus1").startsWith(">locus1\n")).toBe(true);
  });
});

describe("buildGuideFeatures", () => {
  it("emits a protospacer + PAM feature per guide, passing forward coords through", () => {
    const features = buildGuideFeatures(makeReport([makeGuide()]));
    expect(features).toHaveLength(2);

    const proto = features.find((f) => f.kind === "protospacer")!;
    expect(proto).toMatchObject({
      chr: TARGET_CONTIG,
      start: 10,
      end: 30,
      strand: "+",
      name: "g1 protospacer",
    });

    const pam = features.find((f) => f.kind === "pam")!;
    expect(pam).toMatchObject({ chr: TARGET_CONTIG, start: 30, end: 33, strand: "+" });
    expect(pam.name).toContain("AGG"); // PAM sequence surfaced in the label
  });

  it("colors the protospacer by recommendation label; PAM uses a fixed color", () => {
    const preferred = buildGuideFeatures(makeReport([makeGuide({ recommendation_label: "preferred" })]));
    const avoid = buildGuideFeatures(makeReport([makeGuide({ recommendation_label: "avoid" })]));
    const pColor = preferred.find((f) => f.kind === "protospacer")!.color;
    const aColor = avoid.find((f) => f.kind === "protospacer")!.color;
    expect(pColor).not.toBe(aColor);
    // PAM color is independent of label.
    expect(preferred.find((f) => f.kind === "pam")!.color).toBe(
      avoid.find((f) => f.kind === "pam")!.color,
    );
  });

  it("preserves the minus-strand flag and forward coordinates", () => {
    // A '-' guide: backend emits forward-strand coords with PAM 5' of the protospacer.
    const minus = makeGuide({
      strand: "-",
      protospacer_start: 23,
      protospacer_end: 43,
      pam_start: 20,
      pam_end: 23,
    });
    const features = buildGuideFeatures(makeReport([minus]));
    for (const f of features) expect(f.strand).toBe("-");
    expect(features.find((f) => f.kind === "protospacer")).toMatchObject({ start: 23, end: 43 });
    expect(features.find((f) => f.kind === "pam")).toMatchObject({ start: 20, end: 23 });
  });

  it("emits a 1-bp cut feature only when the edit outcome was simulated", () => {
    const withCut = makeGuide({
      edit_outcome_summary: {
        cut_position_fwd: 27,
        frameshift_probability: 0.4,
        no_edit_probability: 0.5,
        top_outcomes: [],
      },
    });
    const features = buildGuideFeatures(makeReport([withCut]));
    const cut = features.find((f) => f.kind === "cut")!;
    expect(cut).toMatchObject({ start: 27, end: 28, chr: TARGET_CONTIG });

    // No edit_outcome_summary => no cut feature (we never guess the position).
    const noCut = buildGuideFeatures(makeReport([makeGuide({ edit_outcome_summary: null })]));
    expect(noCut.some((f) => f.kind === "cut")).toBe(false);
  });

  it("tags features with the guide rank so multiple guides stay distinguishable", () => {
    const features = buildGuideFeatures(
      makeReport([makeGuide({ rank: 1 }), makeGuide({ rank: 2 })]),
    );
    expect(features.filter((f) => f.name.startsWith("g1 "))).toHaveLength(2);
    expect(features.filter((f) => f.name.startsWith("g2 "))).toHaveLength(2);
  });
});

describe("guideLocus", () => {
  it("converts 0-based half-open coords to a 1-based inclusive igv locus, with flank", () => {
    // protospacer [10,30), PAM [30,33) -> span [10,33) -> 1-based [11,33], padded by flank.
    expect(guideLocus(makeGuide(), TARGET_CONTIG, 0)).toBe("target:11-33");
    expect(guideLocus(makeGuide(), TARGET_CONTIG, 8)).toBe("target:3-41");
  });

  it("spans protospacer through PAM regardless of strand (uses min start / max end)", () => {
    // A '-' guide: PAM sits 5' of the protospacer, so min/max still bracket the whole guide.
    const minus = makeGuide({
      strand: "-",
      protospacer_start: 23,
      protospacer_end: 43,
      pam_start: 20,
      pam_end: 23,
    });
    expect(guideLocus(minus, TARGET_CONTIG, 0)).toBe("target:21-43");
  });

  it("clamps the lower bound to 1 (igv loci are 1-based, never 0 or negative)", () => {
    const atStart = makeGuide({
      protospacer_start: 0,
      protospacer_end: 20,
      pam_start: 20,
      pam_end: 23,
    });
    expect(guideLocus(atStart, TARGET_CONTIG, 8)).toBe("target:1-31");
  });

  it("honors a custom contig name", () => {
    expect(guideLocus(makeGuide(), "locus1", 0).startsWith("locus1:")).toBe(true);
  });
});

describe("buildIgvConfig", () => {
  it("builds a non-indexed inline-FASTA reference and a single guide track", () => {
    const report = makeReport([makeGuide()]);
    const config = buildIgvConfig(report, "blob:fake-url");
    expect(config.reference).toMatchObject({
      id: TARGET_CONTIG,
      fastaURL: "blob:fake-url",
      indexed: false,
    });
    expect(config.tracks).toHaveLength(1);
    expect(config.tracks[0].type).toBe("annotation");
    expect((config.tracks[0].features as unknown[]).length).toBe(2);
    expect(config.locus).toBe(TARGET_CONTIG);
  });
});
