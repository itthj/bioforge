import { useEffect, useRef, useState } from "react";
import { getAccuracyReport } from "../api/benchmarks";
import { ApiError } from "../api/projects";
import type {
  AccuracyReport as AccuracyReportData,
  BenchmarkWiring,
  PublishedBenchmark,
  PublishedEditOutcomeBenchmark,
  PublishedGiabBenchmark,
  ValidatorGate,
} from "../types/benchmarks";
import { downloadBlob, svgToString, toCsv } from "../lib/download";
import { ReliabilityDiagram } from "./ReliabilityDiagram";
import { ExportButton } from "./ui/ExportButton";

/** lowercase a benchmark name into a filesystem-safe slug for export filenames. */
function slug(name: string): string {
  return name.replace(/[^a-z0-9]+/gi, "_").replace(/^_+|_+$/g, "").toLowerCase() || "histogram";
}

/** Container: fetches the live report on mount, handles loading/error. */
export function AccuracyReport() {
  const [report, setReport] = useState<AccuracyReportData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getAccuracyReport()
      .then((r) => {
        if (!cancelled) setReport(r);
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof ApiError ? `${e.status}: ${e.detail}` : String(e));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <div className="rounded-md border border-dashed border-border bg-surface p-6 text-center text-sm text-fg-subtle">
        Measuring…
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded border border-border bg-surface-2 p-3 text-sm text-danger">
        Could not load the accuracy report — {error}
      </div>
    );
  }
  if (!report) return null;
  return <AccuracyReportView report={report} />;
}

/** Presentational: renders a fetched report. Pure → trivially testable by content. */
export function AccuracyReportView({ report }: { report: AccuracyReportData }) {
  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-xs font-semibold uppercase tracking-wider text-fg-subtle">
          Accuracy Report
        </h2>
        <p className="mt-0.5 text-xs text-fg-subtle">
          BioForge measures its own accuracy and publishes it here. Numbers below are
          really computed; benchmarks not yet wired are marked as such — never faked.
          <span className="ml-1 font-mono">v{report.bioforge_version}</span>
        </p>
      </div>

      <ValidatorGateCard gate={report.validator} />
      <ModelAccuracySection models={report.models} />
      <BenchmarkLedger benchmarks={report.benchmarks} />
      <PublishedResults published={report.published} />
      <GiabConcordanceResults published={report.published_giab} />
      <EditOutcomeResults published={report.published_edit_outcome} />
    </div>
  );
}

/** §13 / Phase 2: real, dated edit-outcome distribution agreement — FORECasT predicted vs measured
 *  indel profiles (TVD/JSD), generated offline (a network fetch + an out-of-process FORECasT run),
 *  never on page load. */
function EditOutcomeResults({ published }: { published: PublishedEditOutcomeBenchmark[] }) {
  if (published.length === 0) return null;
  return (
    <section className="rounded-md border border-border bg-surface p-4 shadow-sm">
      <h3 className="text-sm font-semibold text-fg">
        Edit-outcome distribution agreement{" "}
        <span className="font-normal text-fg-subtle">· §13 / Phase 2</span>
      </h3>
      <div className="mt-3 space-y-4">
        {published.map((eo) => {
          const leak = LEAKAGE_STYLES[eo.leakage_status] ?? LEAKAGE_STYLES.unknown;
          return (
            <div key={eo.name} className="rounded border border-border p-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="text-sm font-medium text-fg">{eo.name}</span>
                <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${leak.classes}`}>
                  {leak.label}
                </span>
              </div>
              <div className="mt-2 flex flex-wrap gap-x-6 gap-y-1 font-mono text-sm text-fg">
                <span>
                  <span className="text-fg-subtle">median TVD</span> {eo.tvd_median.toFixed(3)}
                  <span className="ml-1 text-[11px] text-fg-subtle">
                    (IQR {eo.tvd_q1.toFixed(3)}–{eo.tvd_q3.toFixed(3)})
                  </span>
                </span>
                <span>
                  <span className="text-fg-subtle">median JSD</span> {eo.jsd_median.toFixed(3)}
                </span>
                <span>
                  <span className="text-fg-subtle">n</span> {eo.n_guides}
                </span>
              </div>
              <div className="mt-1 font-mono text-[11px] text-fg-subtle">
                {eo.sample} · {eo.direction}-strand · ≥{eo.min_reads} reads · {eo.model_version} ·
                measured {new Date(eo.generated_at).toISOString().slice(0, 10)}
              </div>
              <TvdHistogram bins={eo.tvd_histogram} name={eo.name} />
              <p className="mt-2 text-[11px] text-fg-muted">{eo.interpretation}</p>
              <p className="mt-1 text-[11px] italic text-warn">{eo.leakage_caveat}</p>
            </div>
          );
        })}
      </div>
    </section>
  );
}

/** Per-guide TVD distribution as a lean inline-SVG histogram (no chart dependency). Lower TVD =
 *  better agreement, so mass toward the left is good. */
function TvdHistogram({
  bins,
  name,
}: {
  bins: PublishedEditOutcomeBenchmark["tvd_histogram"];
  name: string;
}) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  if (bins.length === 0) return null;
  const max = Math.max(1, ...bins.map((b) => b.count));
  const W = 240;
  const H = 60;
  const bw = W / bins.length;
  const exportCsv = () => {
    const header = ["tvd_lo", "tvd_hi", "count"];
    const rows = bins.map((b) => [b.lo, b.hi, b.count]);
    downloadBlob(`${slug(name)}_tvd_histogram.csv`, "text/csv;charset=utf-8", toCsv([header, ...rows]));
  };
  return (
    <figure className="mt-2">
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H + 14}`} className="w-full max-w-xs" role="img" aria-label="Per-guide TVD distribution">
        {bins.map((b, i) => {
          const h = (b.count / max) * H;
          return (
            <rect
              key={b.lo}
              x={i * bw + 1}
              y={H - h}
              width={bw - 2}
              height={h}
              className="fill-accent"
            >
              <title>
                TVD {b.lo.toFixed(1)}–{b.hi.toFixed(1)}: {b.count} guides
              </title>
            </rect>
          );
        })}
        <line x1={0} y1={H} x2={W} y2={H} className="stroke-border" strokeWidth={1} />
        <text x={0} y={H + 11} className="fill-fg-subtle text-[8px]">
          TVD 0
        </text>
        <text x={W} y={H + 11} textAnchor="end" className="fill-fg-subtle text-[8px]">
          1
        </text>
      </svg>
      <figcaption className="flex items-center gap-3 text-[10px] text-fg-subtle">
        <span>Per-guide TVD (left = better agreement)</span>
        <ExportButton label="CSV" title="Download the TVD histogram bins as CSV" onClick={exportCsv} />
        <ExportButton
          label="SVG"
          title="Download the TVD histogram as SVG"
          onClick={() => {
            if (svgRef.current) {
              downloadBlob(
                `${slug(name)}_tvd_histogram.svg`,
                "image/svg+xml;charset=utf-8",
                svgToString(svgRef.current),
              );
            }
          }}
        />
      </figcaption>
    </figure>
  );
}

/** §13 / Phase 3: real, dated GIAB variant-calling concordance (precision/recall/F1 by class),
 *  generated offline by a real DeepVariant run vs a NIST/GIAB truth set. Never computed on load. */
function GiabConcordanceResults({ published }: { published: PublishedGiabBenchmark[] }) {
  if (published.length === 0) return null;
  return (
    <section className="rounded-md border border-border bg-surface p-4 shadow-sm">
      <h3 className="text-sm font-semibold text-fg">
        GIAB variant-calling concordance{" "}
        <span className="font-normal text-fg-subtle">· §13 / Phase 3</span>
      </h3>
      <div className="mt-3 space-y-4">
        {published.map((gb) => (
          <div key={gb.name} className="rounded border border-border p-3">
            <div className="text-sm font-medium text-fg">{gb.name}</div>
            <div className="mt-0.5 font-mono text-[11px] text-fg-subtle">
              {gb.sample} · {gb.regions} · {gb.reference_build}
            </div>
            <table className="mt-2 w-full text-left text-xs">
              <thead>
                <tr className="text-fg-subtle">
                  <th className="py-0.5 pr-3 font-medium">class</th>
                  <th className="py-0.5 pr-3 font-medium">precision</th>
                  <th className="py-0.5 pr-3 font-medium">recall</th>
                  <th className="py-0.5 pr-3 font-medium">F1</th>
                  <th className="py-0.5 pr-3 font-medium">TP/FP/FN</th>
                </tr>
              </thead>
              <tbody className="font-mono text-fg">
                {gb.by_class.map((m) => (
                  <tr key={m.variant_class}>
                    <td className="py-0.5 pr-3">{m.variant_class}</td>
                    <td className="py-0.5 pr-3">{m.precision.toFixed(4)}</td>
                    <td className="py-0.5 pr-3">{m.recall.toFixed(4)}</td>
                    <td className="py-0.5 pr-3">{m.f1.toFixed(4)}</td>
                    <td className="py-0.5 pr-3 text-fg-subtle">
                      {m.tp}/{m.fp}/{m.fn}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="mt-2 text-[11px] text-fg-subtle">
              {gb.n_truth_in_regions} truth variants in confident regions ·{" "}
              {gb.n_called_in_regions} called · caller {gb.caller}
            </div>
            <p className="mt-1 text-[11px] text-fg-muted">{gb.interpretation}</p>
            <p className="mt-1 text-[11px] italic text-warn">{gb.caveat}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

const LEAKAGE_STYLES: Record<string, { label: string; classes: string }> = {
  held_out: { label: "held-out", classes: "bg-surface-2 text-success" },
  unknown: { label: "leakage unverified", classes: "bg-surface-2 text-warn" },
  contaminated: { label: "contaminated", classes: "bg-surface-2 text-danger" },
};

/** §6 / §13: real, dated benchmark measurements + the reliability diagram behind each. Generated
 *  offline (a run is a network fetch + a Docker call), never computed on page load. */
function PublishedResults({ published }: { published: PublishedBenchmark[] }) {
  if (published.length === 0) {
    return (
      <section className="rounded-md border border-border bg-surface p-4 shadow-sm">
        <h3 className="text-sm font-semibold text-fg">
          Published results <span className="font-normal text-fg-subtle">· §6 / §13 calibration</span>
        </h3>
        <p className="mt-1 text-xs text-fg-subtle">
          No benchmark run has been published yet. A run is a network fetch + an out-of-process model
          call (never on page load); once generated offline, its measured correlation and the
          reliability curve behind it appear here.
        </p>
      </section>
    );
  }
  return (
    <section className="space-y-4">
      <h3 className="text-sm font-semibold text-fg">
        Published results{" "}
        <span className="font-normal text-fg-subtle">· §6 / §13 — real, dated measurements</span>
      </h3>
      {published.map((pb) => {
        const leak = LEAKAGE_STYLES[pb.leakage_status] ?? LEAKAGE_STYLES.unknown;
        return (
          <div key={pb.name} className="space-y-2">
            <div className="rounded-md border border-border bg-surface p-4 shadow-sm">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="text-sm font-semibold text-fg">{pb.name}</span>
                <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${leak.classes}`}>
                  {leak.label}
                </span>
              </div>
              <div className="mt-2 flex flex-wrap gap-x-6 gap-y-1 font-mono text-sm text-fg">
                <span>
                  <span className="text-fg-subtle">Spearman ρ</span> {pb.spearman_rho.toFixed(3)}
                </span>
                <span>
                  <span className="text-fg-subtle">Pearson r</span> {pb.pearson_r.toFixed(3)}
                </span>
                <span>
                  <span className="text-fg-subtle">n</span> {pb.n}
                </span>
              </div>
              <div className="mt-1 text-[11px] text-fg-subtle">
                {pb.model_version} · data sha256 {pb.data_sha256.slice(0, 12)}… · measured{" "}
                {new Date(pb.generated_at).toISOString().slice(0, 10)}
              </div>
              <p className="mt-2 text-[11px] text-fg-subtle">{pb.interpretation}</p>
            </div>
            <ReliabilityDiagram curve={pb.reliability} />
          </div>
        );
      })}
    </section>
  );
}

function pct(x: number): string {
  return `${(x * 100).toFixed(1)}%`;
}

function ValidatorGateCard({ gate }: { gate: ValidatorGate }) {
  const m = gate.metrics;
  const rows = [
    {
      layer: "Numeric (L3)",
      precision: m.numeric_block_precision,
      recall: m.numeric_fabrication_recall,
      passes: gate.numeric_passes,
    },
    {
      layer: "Identifier (L3+)",
      precision: m.entity_block_precision,
      recall: m.entity_fabrication_recall,
      passes: gate.entity_passes,
    },
  ];
  return (
    <section className="rounded-md border border-border bg-surface p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-fg">
          Grounding validator <span className="font-normal text-fg-subtle">· Layer 6</span>
        </h3>
        <span
          className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-semibold ${
            gate.passes ? "bg-surface-2 text-success" : "bg-surface-2 text-danger"
          }`}
        >
          release gate: {gate.passes ? "PASS" : "FAIL"}
        </span>
      </div>
      <p className="mt-1 text-xs text-fg-subtle">
        Measured over {m.n_cases} hand-labeled cases. Deterministic layers must hit the{" "}
        {pct(gate.threshold)} threshold on both block precision and fabrication recall.
      </p>
      <table className="mt-3 w-full text-sm">
        <thead>
          <tr className="text-left text-[11px] uppercase tracking-wide text-fg-subtle">
            <th className="py-1 font-medium">Layer</th>
            <th className="py-1 font-medium">Block precision</th>
            <th className="py-1 font-medium">Fabrication recall</th>
            <th className="py-1 font-medium" />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.layer} className="border-t border-border">
              <td className="py-1.5 text-fg-muted">{r.layer}</td>
              <td className="py-1.5 font-mono text-fg">{pct(r.precision)}</td>
              <td className="py-1.5 font-mono text-fg">{pct(r.recall)}</td>
              <td className="py-1.5">
                <span
                  className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium ${
                    r.passes ? "bg-surface-2 text-success" : "bg-surface-2 text-danger"
                  }`}
                >
                  {r.passes ? "pass" : "fail"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function ModelAccuracySection({
  models,
}: {
  models: AccuracyReportData["models"];
}) {
  return (
    <section className="rounded-md border border-border bg-surface p-4 shadow-sm">
      <h3 className="text-sm font-semibold text-fg">
        Model accuracy provenance{" "}
        <span className="font-normal text-fg-subtle">· published, cited, never invented</span>
      </h3>
      {models.length === 0 ? (
        <p className="mt-2 text-sm text-fg-subtle">No models carry accuracy metadata.</p>
      ) : (
        <ul className="mt-3 space-y-3">
          {models.map((model) => (
            <li key={model.tool} className="border-t border-border pt-3 first:border-t-0 first:pt-0">
              <div className="font-mono text-sm font-semibold text-fg">{model.tool}</div>
              <KeyVals label="version" map={model.model_versions} mono />
              <KeyVals label="published accuracy" map={model.published_accuracy} />
              <div className="mt-1 text-xs text-fg-subtle">
                instance-level uncertainty:{" "}
                {Object.keys(model.emits_instance_uncertainty).length === 0 ? (
                  <span className="italic text-fg-subtle">n/a</span>
                ) : (
                  Object.entries(model.emits_instance_uncertainty).map(([k, v]) => (
                    <span key={k} className="mr-2 font-mono">
                      {k}={v ? "emitted" : "point estimate only"}
                    </span>
                  ))
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function KeyVals({
  label,
  map,
  mono,
}: {
  label: string;
  map: Record<string, string>;
  mono?: boolean;
}) {
  const entries = Object.entries(map);
  if (entries.length === 0) return null;
  return (
    <div className="mt-1 text-xs text-fg-muted">
      <span className="text-fg-subtle">{label}: </span>
      {entries.map(([k, v]) => (
        <span key={k} className="mr-3">
          <span className="text-fg-subtle">{k}</span>{" "}
          <span className={mono ? "font-mono text-fg" : "text-fg"}>{v}</span>
        </span>
      ))}
    </div>
  );
}

const WIRING_STYLES: Record<BenchmarkWiring, { label: string; classes: string }> = {
  live: { label: "live", classes: "bg-surface-2 text-success" },
  guard_only: { label: "guard only", classes: "bg-surface-2 text-warn" },
  not_yet_wired: { label: "not yet wired", classes: "bg-surface-2 text-fg-muted" },
};

function BenchmarkLedger({
  benchmarks,
}: {
  benchmarks: AccuracyReportData["benchmarks"];
}) {
  return (
    <section className="rounded-md border border-border bg-surface p-4 shadow-sm">
      <h3 className="text-sm font-semibold text-fg">
        Gold-standard benchmarks <span className="font-normal text-fg-subtle">· §13</span>
      </h3>
      <ul className="mt-3 space-y-2">
        {benchmarks.map((b) => {
          const style = WIRING_STYLES[b.status];
          return (
            <li key={b.name} className="rounded border border-border bg-bg/50 p-2.5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-sm text-fg">{b.name}</div>
                  <div className="mt-0.5 text-[11px] font-mono text-fg-subtle">{b.blueprint_section}</div>
                </div>
                <span
                  className={`shrink-0 inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium ${style.classes}`}
                >
                  {style.label}
                </span>
              </div>
              <p className="mt-1 text-xs text-fg-subtle">{b.detail}</p>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
