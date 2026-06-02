import { useEffect, useState } from "react";
import { getAccuracyReport } from "../api/benchmarks";
import { ApiError } from "../api/projects";
import type {
  AccuracyReport as AccuracyReportData,
  BenchmarkWiring,
  PublishedBenchmark,
  ValidatorGate,
} from "../types/benchmarks";
import { ReliabilityDiagram } from "./ReliabilityDiagram";

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
      <div className="rounded-md border border-dashed border-slate-300 bg-white p-6 text-center text-sm text-slate-400">
        Measuring…
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded border border-rose-300 bg-rose-50 p-3 text-sm text-rose-800">
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
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
          Accuracy Report
        </h2>
        <p className="mt-0.5 text-xs text-slate-400">
          BioForge measures its own accuracy and publishes it here. Numbers below are
          really computed; benchmarks not yet wired are marked as such — never faked.
          <span className="ml-1 font-mono">v{report.bioforge_version}</span>
        </p>
      </div>

      <ValidatorGateCard gate={report.validator} />
      <ModelAccuracySection models={report.models} />
      <BenchmarkLedger benchmarks={report.benchmarks} />
      <PublishedResults published={report.published} />
    </div>
  );
}

const LEAKAGE_STYLES: Record<string, { label: string; classes: string }> = {
  held_out: { label: "held-out", classes: "bg-emerald-100 text-emerald-800" },
  unknown: { label: "leakage unverified", classes: "bg-amber-100 text-amber-800" },
  contaminated: { label: "contaminated", classes: "bg-rose-100 text-rose-800" },
};

/** §6 / §13: real, dated benchmark measurements + the reliability diagram behind each. Generated
 *  offline (a run is a network fetch + a Docker call), never computed on page load. */
function PublishedResults({ published }: { published: PublishedBenchmark[] }) {
  if (published.length === 0) {
    return (
      <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
        <h3 className="text-sm font-semibold text-slate-900">
          Published results <span className="font-normal text-slate-400">· §6 / §13 calibration</span>
        </h3>
        <p className="mt-1 text-xs text-slate-500">
          No benchmark run has been published yet. A run is a network fetch + an out-of-process model
          call (never on page load); once generated offline, its measured correlation and the
          reliability curve behind it appear here.
        </p>
      </section>
    );
  }
  return (
    <section className="space-y-4">
      <h3 className="text-sm font-semibold text-slate-900">
        Published results{" "}
        <span className="font-normal text-slate-400">· §6 / §13 — real, dated measurements</span>
      </h3>
      {published.map((pb) => {
        const leak = LEAKAGE_STYLES[pb.leakage_status] ?? LEAKAGE_STYLES.unknown;
        return (
          <div key={pb.name} className="space-y-2">
            <div className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="text-sm font-semibold text-slate-900">{pb.name}</span>
                <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${leak.classes}`}>
                  {leak.label}
                </span>
              </div>
              <div className="mt-2 flex flex-wrap gap-x-6 gap-y-1 font-mono text-sm text-slate-900">
                <span>
                  <span className="text-slate-400">Spearman ρ</span> {pb.spearman_rho.toFixed(3)}
                </span>
                <span>
                  <span className="text-slate-400">Pearson r</span> {pb.pearson_r.toFixed(3)}
                </span>
                <span>
                  <span className="text-slate-400">n</span> {pb.n}
                </span>
              </div>
              <div className="mt-1 text-[11px] text-slate-400">
                {pb.model_version} · data sha256 {pb.data_sha256.slice(0, 12)}… · measured{" "}
                {new Date(pb.generated_at).toISOString().slice(0, 10)}
              </div>
              <p className="mt-2 text-[11px] text-slate-500">{pb.interpretation}</p>
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
    <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-900">
          Grounding validator <span className="font-normal text-slate-400">· Layer 6</span>
        </h3>
        <span
          className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-semibold ${
            gate.passes ? "bg-emerald-100 text-emerald-800" : "bg-rose-100 text-rose-800"
          }`}
        >
          release gate: {gate.passes ? "PASS" : "FAIL"}
        </span>
      </div>
      <p className="mt-1 text-xs text-slate-400">
        Measured over {m.n_cases} hand-labeled cases. Deterministic layers must hit the{" "}
        {pct(gate.threshold)} threshold on both block precision and fabrication recall.
      </p>
      <table className="mt-3 w-full text-sm">
        <thead>
          <tr className="text-left text-[11px] uppercase tracking-wide text-slate-400">
            <th className="py-1 font-medium">Layer</th>
            <th className="py-1 font-medium">Block precision</th>
            <th className="py-1 font-medium">Fabrication recall</th>
            <th className="py-1 font-medium" />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.layer} className="border-t border-slate-100">
              <td className="py-1.5 text-slate-700">{r.layer}</td>
              <td className="py-1.5 font-mono text-slate-900">{pct(r.precision)}</td>
              <td className="py-1.5 font-mono text-slate-900">{pct(r.recall)}</td>
              <td className="py-1.5">
                <span
                  className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium ${
                    r.passes ? "bg-emerald-100 text-emerald-800" : "bg-rose-100 text-rose-800"
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
    <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
      <h3 className="text-sm font-semibold text-slate-900">
        Model accuracy provenance{" "}
        <span className="font-normal text-slate-400">· published, cited, never invented</span>
      </h3>
      {models.length === 0 ? (
        <p className="mt-2 text-sm text-slate-400">No models carry accuracy metadata.</p>
      ) : (
        <ul className="mt-3 space-y-3">
          {models.map((model) => (
            <li key={model.tool} className="border-t border-slate-100 pt-3 first:border-t-0 first:pt-0">
              <div className="font-mono text-sm font-semibold text-slate-900">{model.tool}</div>
              <KeyVals label="version" map={model.model_versions} mono />
              <KeyVals label="published accuracy" map={model.published_accuracy} />
              <div className="mt-1 text-xs text-slate-500">
                instance-level uncertainty:{" "}
                {Object.keys(model.emits_instance_uncertainty).length === 0 ? (
                  <span className="italic text-slate-400">n/a</span>
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
    <div className="mt-1 text-xs text-slate-600">
      <span className="text-slate-400">{label}: </span>
      {entries.map(([k, v]) => (
        <span key={k} className="mr-3">
          <span className="text-slate-500">{k}</span>{" "}
          <span className={mono ? "font-mono text-slate-800" : "text-slate-800"}>{v}</span>
        </span>
      ))}
    </div>
  );
}

const WIRING_STYLES: Record<BenchmarkWiring, { label: string; classes: string }> = {
  live: { label: "live", classes: "bg-emerald-100 text-emerald-800" },
  guard_only: { label: "guard only", classes: "bg-amber-100 text-amber-800" },
  not_yet_wired: { label: "not yet wired", classes: "bg-slate-200 text-slate-600" },
};

function BenchmarkLedger({
  benchmarks,
}: {
  benchmarks: AccuracyReportData["benchmarks"];
}) {
  return (
    <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
      <h3 className="text-sm font-semibold text-slate-900">
        Gold-standard benchmarks <span className="font-normal text-slate-400">· §13</span>
      </h3>
      <ul className="mt-3 space-y-2">
        {benchmarks.map((b) => {
          const style = WIRING_STYLES[b.status];
          return (
            <li key={b.name} className="rounded border border-slate-100 bg-slate-50/50 p-2.5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-sm text-slate-800">{b.name}</div>
                  <div className="mt-0.5 text-[11px] font-mono text-slate-400">{b.blueprint_section}</div>
                </div>
                <span
                  className={`shrink-0 inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium ${style.classes}`}
                >
                  {style.label}
                </span>
              </div>
              <p className="mt-1 text-xs text-slate-500">{b.detail}</p>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
