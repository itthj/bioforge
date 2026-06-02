/**
 * Tests for AccuracyReportView.
 *
 * Prop-driven like CrisprReportCard: we assert the right self-measurement surfaces —
 * the release-gate verdict, the real precision/recall, model provenance, and the honest
 * benchmark ledger (including a "not yet wired" entry, which is the whole point: the
 * platform never fakes an unmeasured number).
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AccuracyReportView } from "../AccuracyReport";
import type { AccuracyReport } from "../../types/benchmarks";

function makeReport(overrides: Partial<AccuracyReport> = {}): AccuracyReport {
  return {
    generated_at: "2026-05-29T00:00:00Z",
    bioforge_version: "0.1.0",
    validator: {
      threshold: 1.0,
      numeric_passes: true,
      entity_passes: true,
      passes: true,
      metrics: {
        n_cases: 16,
        numeric_true_positives: 5,
        numeric_false_positives: 0,
        numeric_false_negatives: 0,
        numeric_block_precision: 1.0,
        numeric_fabrication_recall: 1.0,
        entity_true_positives: 4,
        entity_false_positives: 0,
        entity_false_negatives: 0,
        entity_block_precision: 1.0,
        entity_fabrication_recall: 1.0,
      },
    },
    models: [
      {
        tool: "find_offtargets",
        model_versions: { off_target: "Hsu-2013-MIT" },
        published_accuracy: { off_target: "see Hsu et al. 2013" },
        emits_instance_uncertainty: { off_target: false },
      },
    ],
    benchmarks: [
      {
        name: "Grounding validator — numeric (L3)",
        blueprint_section: "§4 L6 / §13",
        status: "live",
        detail: "Real corpus metric.",
      },
      {
        name: "ClinVar interpretation fidelity (>=2 star)",
        blueprint_section: "§13",
        status: "guard_only",
        detail: "Guard unit-tested; live gold-set pending.",
      },
      {
        name: "Variant calling — GIAB precision / recall / F1",
        blueprint_section: "§13 / Phase 3",
        status: "not_yet_wired",
        detail: "No variant-calling path built yet.",
      },
    ],
    published: [],
    published_giab: [],
    ...overrides,
  };
}

function makeGiab() {
  return {
    name: "Variant calling: DeepVariant vs NIST/GIAB (NA12878, chr20:10-10.1Mb)",
    blueprint_section: "§13 / Phase 3",
    generated_at: "2026-06-02T00:33:00Z",
    caller: "DeepVariant google/deepvariant@sha256:ccab95 model_type=WGS",
    reference_build: "ucsc.hg19 chr20 (DeepVariant quickstart)",
    regions: "chr20:10000000-10100000",
    sample: "NA12878 (HG001)",
    truth_set: "NIST/GIAB test_nist chr20:10-10.1Mb",
    n_truth_in_regions: 49,
    n_called_in_regions: 50,
    by_class: [
      { variant_class: "SNV" as const, tp: 45, fp: 1, fn: 0, precision: 0.9783, recall: 1.0, f1: 0.989 },
      { variant_class: "INDEL" as const, tp: 4, fp: 0, fn: 0, precision: 1.0, recall: 1.0, f1: 1.0 },
      { variant_class: "ALL" as const, tp: 49, fp: 1, fn: 0, precision: 0.98, recall: 1.0, f1: 0.9899 },
    ],
    caveat: "genotype-agnostic, not haplotype-aware like hap.py",
    interpretation: "small validation region, not a genome-wide HG002 claim",
  };
}

function makePublished() {
  return {
    name: "CRISPR on-target: DeepCRISPR vs Chari-2015 (held-out, cross-dataset)",
    blueprint_section: "§13 / Phase 2",
    generated_at: "2026-06-02T01:01:33Z",
    model_version: "ontar_cnn_reg_seq@master",
    dataset: "chari2015Train",
    data_sha256: "6a6254a3966c53aa5eceb46cddf57e940466632ebee277d7b0450b662485e576",
    citation: "Chari R et al. (2015) Nat Methods 12:823-826",
    n: 1234,
    spearman_rho: 0.1299,
    pearson_r: 0.1162,
    leakage_status: "held_out",
    leakage_evidence: "Chuai 2018 (PMC6020378): Chari 2015 is independent validation, not training.",
    leakage_caveat: "Incidental guide overlap not sequence-level checked.",
    dataset_relationship: "cross_dataset",
    interpretation: "Spearman rho=0.130 between predicted score and measured efficiency.",
    reliability: {
      n: 1234,
      n_bins: 2,
      bins: [
        { bin_index: 0, n: 617, predicted_mean: 0.15, observed_mean: 0.9, observed_sem: 0.05, predicted_low: 0.1, predicted_high: 0.2 },
        { bin_index: 1, n: 617, predicted_mean: 0.35, observed_mean: 1.2, observed_sem: 0.06, predicted_low: 0.3, predicted_high: 0.44 },
      ],
      monotonicity_rho: 1.0,
      kind: "regression_ranking" as const,
      predicted_label: "DeepCRISPR on-target score",
      observed_label: "Chari-2015 measured efficiency",
      caveat: "the score is not a probability calibration",
    },
  };
}

describe("AccuracyReportView", () => {
  it("renders the header and version", () => {
    render(<AccuracyReportView report={makeReport()} />);
    expect(screen.getByText(/Accuracy Report/i)).toBeInTheDocument();
    expect(screen.getByText(/v0\.1\.0/)).toBeInTheDocument();
  });

  it("shows the validator release-gate verdict and real precision/recall", () => {
    render(<AccuracyReportView report={makeReport()} />);
    expect(screen.getByText(/release gate: PASS/i)).toBeInTheDocument();
    // Both layers at 100% precision + recall → four 100.0% cells.
    expect(screen.getAllByText("100.0%").length).toBeGreaterThanOrEqual(4);
    expect(screen.getByText(/16 hand-labeled cases/i)).toBeInTheDocument();
  });

  it("renders a FAIL badge when the gate does not pass", () => {
    const failing = makeReport();
    failing.validator.passes = false;
    failing.validator.numeric_passes = false;
    failing.validator.metrics.numeric_block_precision = 0.5;
    render(<AccuracyReportView report={failing} />);
    expect(screen.getByText(/release gate: FAIL/i)).toBeInTheDocument();
  });

  it("lists model accuracy provenance from the registry", () => {
    render(<AccuracyReportView report={makeReport()} />);
    expect(screen.getByText("find_offtargets")).toBeInTheDocument();
    expect(screen.getByText(/Hsu-2013-MIT/)).toBeInTheDocument();
    expect(screen.getByText(/point estimate only/i)).toBeInTheDocument();
  });

  it("renders the honest benchmark ledger with wiring statuses", () => {
    render(<AccuracyReportView report={makeReport()} />);
    expect(screen.getByText(/GIAB/i)).toBeInTheDocument();
    expect(screen.getByText("live")).toBeInTheDocument();
    expect(screen.getByText("guard only")).toBeInTheDocument();
    // The point of the report: it admits what it has NOT measured.
    expect(screen.getByText("not yet wired")).toBeInTheDocument();
  });

  it("shows the honest empty-state when no benchmark run is published", () => {
    render(<AccuracyReportView report={makeReport()} />);
    expect(screen.getByText(/No benchmark run has been published yet/i)).toBeInTheDocument();
  });

  it("renders a published benchmark's headline metrics and its reliability diagram", () => {
    const withPublished = makeReport({ published: [makePublished()] });
    render(<AccuracyReportView report={withPublished} />);
    // The real, dated measurement surfaces: name, the held-out leakage badge, and the run date.
    expect(
      screen.getByText("CRISPR on-target: DeepCRISPR vs Chari-2015 (held-out, cross-dataset)"),
    ).toBeInTheDocument();
    expect(screen.getByText("held-out")).toBeInTheDocument(); // sourced leakage badge
    expect(screen.getByText(/measured 2026-06-02/i)).toBeInTheDocument(); // dated, not a live computation
    // The reliability diagram behind the number is rendered for the published curve.
    expect(screen.getByRole("img", { name: /reliability curve/i })).toBeInTheDocument();
    expect(screen.getByText(/monotonicity/i)).toBeInTheDocument();
  });

  it("renders the GIAB concordance section with per-class precision/recall when published", () => {
    const withGiab = makeReport({ published_giab: [makeGiab()] });
    render(<AccuracyReportView report={withGiab} />);
    expect(screen.getByText(/GIAB variant-calling concordance/i)).toBeInTheDocument();
    expect(
      screen.getByText("Variant calling: DeepVariant vs NIST/GIAB (NA12878, chr20:10-10.1Mb)"),
    ).toBeInTheDocument();
    expect(screen.getByText("SNV")).toBeInTheDocument();
    expect(screen.getByText("INDEL")).toBeInTheDocument();
    // The ALL-class precision (0.98 -> 0.9800) surfaces.
    expect(screen.getAllByText("0.9800").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/not haplotype-aware/i)).toBeInTheDocument();
  });

  it("omits the GIAB section entirely when nothing is published (no faked row)", () => {
    render(<AccuracyReportView report={makeReport()} />);
    expect(screen.queryByText(/GIAB variant-calling concordance/i)).not.toBeInTheDocument();
  });
});
