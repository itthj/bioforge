import { useRef } from "react";
import type { CalibrationCurve } from "../types/benchmarks";
import { downloadBlob, svgToString, toCsv } from "../lib/download";
import { ExportButton } from "./ui/ExportButton";

interface CalibrationDiagramProps {
  curve: CalibrationCurve;
}

/** Per-bin calibration data as CSV — the numbers behind the diagram. */
export function calibrationToCsv(curve: CalibrationCurve): string {
  const header = ["bin_index", "n", "predicted_mean", "observed_freq", "gap"];
  const rows = curve.bins.map((b) => [b.bin_index, b.n, b.predicted_mean, b.observed_freq, b.gap]);
  return toCsv([header, ...rows]);
}

const W = 320;
const H = 220;
const M = { top: 14, right: 14, bottom: 38, left: 44 };

/**
 * Renders a PROBABILITY-calibration diagram: mean predicted probability per bin (x) vs the
 * empirical outcome frequency (y), over a fixed [0,1] x [0,1] frame. Unlike the ranking-reliability
 * curve, y=x IS the target here, so the diagonal is drawn as the perfect-calibration reference.
 * ECE / MCE / Brier summarize the gap. Only the bins the backend produced are rendered.
 */
export function CalibrationDiagram({ curve }: CalibrationDiagramProps) {
  const svgRef = useRef<SVGSVGElement | null>(null);

  // Fixed [0,1] frame so the diagonal is meaningful.
  const px = (x: number) => M.left + x * (W - M.left - M.right);
  const py = (y: number) => H - M.bottom - y * (H - M.top - M.bottom);
  const polyline = curve.bins.map((b) => `${px(b.predicted_mean)},${py(b.observed_freq)}`).join(" ");

  return (
    <section className="rounded-md border border-border bg-surface p-4 shadow-sm">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-fg">
          Calibration diagram <span className="font-normal text-fg-subtle">· §6 / probability</span>
        </h3>
        <div className="flex items-baseline gap-3">
          <span className="font-mono text-xs text-fg-subtle" title="Expected / Maximum Calibration Error; Brier score">
            ECE {curve.ece.toFixed(3)} · MCE {curve.mce.toFixed(3)} · Brier {curve.brier.toFixed(3)}
          </span>
          <ExportButton
            label="CSV"
            title="Download the per-bin calibration data as CSV"
            onClick={() =>
              downloadBlob("calibration_curve.csv", "text/csv;charset=utf-8", calibrationToCsv(curve))
            }
          />
          <ExportButton
            label="SVG"
            title="Download the calibration figure as SVG"
            onClick={() => {
              if (svgRef.current) {
                downloadBlob("calibration_curve.svg", "image/svg+xml;charset=utf-8", svgToString(svgRef.current));
              }
            }}
          />
        </div>
      </div>

      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="mt-2 w-full" role="img" aria-label="calibration diagram">
        {/* axes */}
        <line x1={M.left} y1={H - M.bottom} x2={W - M.right} y2={H - M.bottom} stroke="#2a3038" strokeWidth={1} />
        <line x1={M.left} y1={M.top} x2={M.left} y2={H - M.bottom} stroke="#2a3038" strokeWidth={1} />
        {/* y=x perfect-calibration reference */}
        <line
          x1={px(0)}
          y1={py(0)}
          x2={px(1)}
          y2={py(1)}
          stroke="#6b7480"
          strokeWidth={1}
          strokeDasharray="4 3"
        />
        <polyline points={polyline} fill="none" stroke="#2f9e8f" strokeWidth={1.5} />
        {curve.bins.map((b) => (
          <circle key={b.bin_index} cx={px(b.predicted_mean)} cy={py(b.observed_freq)} r={3} fill="#5ad1c0" />
        ))}
        <text x={(M.left + W - M.right) / 2} y={H - 6} textAnchor="middle" className="fill-fg-subtle text-[10px]">
          {curve.predicted_label}
        </text>
        <text
          x={12}
          y={(M.top + H - M.bottom) / 2}
          textAnchor="middle"
          transform={`rotate(-90 12 ${(M.top + H - M.bottom) / 2})`}
          className="fill-fg-subtle text-[10px]"
        >
          {curve.observed_label}
        </text>
      </svg>

      <table className="mt-2 w-full text-[11px]">
        <thead>
          <tr className="text-left text-fg-subtle">
            <th className="font-medium">bin</th>
            <th className="font-medium">n</th>
            <th className="font-medium">predicted</th>
            <th className="font-medium">observed</th>
            <th className="font-medium">gap</th>
          </tr>
        </thead>
        <tbody>
          {curve.bins.map((b) => (
            <tr key={b.bin_index} className="border-t border-border">
              <td className="text-fg-muted">{b.bin_index}</td>
              <td className="text-fg-muted">{b.n}</td>
              <td className="font-mono text-fg">{b.predicted_mean.toFixed(3)}</td>
              <td className="font-mono text-fg">{b.observed_freq.toFixed(3)}</td>
              <td className="font-mono text-fg">{b.gap.toFixed(3)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className="mt-1 text-[11px] text-fg-subtle">
        {curve.n_bins} bins over {curve.n} predictions · base rate {curve.base_rate.toFixed(3)}
        {curve.kind === "squashed_score" ? " · squashed score (not a native probability)" : ""}. {curve.caveat}
      </p>
    </section>
  );
}
