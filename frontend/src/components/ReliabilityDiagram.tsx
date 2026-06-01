import type { ReliabilityCurve } from "../types/benchmarks";

interface ReliabilityDiagramProps {
  curve: ReliabilityCurve;
}

const W = 320;
const H = 200;
const M = { top: 14, right: 14, bottom: 38, left: 44 };

/**
 * Renders a reliability (calibration) curve: predicted score per quantile bin (x) vs the MEASURED
 * outcome per bin (y), as an inline SVG with per-bin standard-error bars, plus an accessible bin
 * table underneath (the screen-reader-friendly source of truth, not hover-only).
 *
 * Honest by construction (§6 / rule 11): for a `regression_ranking` curve the score is NOT a
 * probability, so there is no y=x reference line — read it for monotonicity (does a higher
 * predicted score track a higher measured outcome). The model's caveat is shown verbatim. Only
 * the bins the backend produced are rendered; nothing is fabricated.
 */
export function ReliabilityDiagram({ curve }: ReliabilityDiagramProps) {
  const yLo = curve.bins.map((b) => b.observed_mean - b.observed_sem);
  const yHi = curve.bins.map((b) => b.observed_mean + b.observed_sem);
  const xMin = Math.min(...curve.bins.map((b) => b.predicted_mean));
  const xMax = Math.max(...curve.bins.map((b) => b.predicted_mean));
  const yMin = Math.min(...yLo);
  const yMax = Math.max(...yHi);
  const xRange = xMax - xMin || 1;
  const yRange = yMax - yMin || 1;

  const px = (x: number) => M.left + ((x - xMin) / xRange) * (W - M.left - M.right);
  const py = (y: number) => H - M.bottom - ((y - yMin) / yRange) * (H - M.top - M.bottom);
  const polyline = curve.bins.map((b) => `${px(b.predicted_mean)},${py(b.observed_mean)}`).join(" ");

  return (
    <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-900">
          Reliability curve <span className="font-normal text-slate-400">· §6 / calibration</span>
        </h3>
        <span className="font-mono text-xs text-slate-500" title="Spearman rho of per-bin predicted vs observed">
          monotonicity ρ = {curve.monotonicity_rho.toFixed(3)}
        </span>
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} className="mt-2 w-full" role="img" aria-label="reliability curve">
        <line x1={M.left} y1={H - M.bottom} x2={W - M.right} y2={H - M.bottom} stroke="#cbd5e1" strokeWidth={1} />
        <line x1={M.left} y1={M.top} x2={M.left} y2={H - M.bottom} stroke="#cbd5e1" strokeWidth={1} />
        <polyline points={polyline} fill="none" stroke="#0ea5e9" strokeWidth={1.5} />
        {curve.bins.map((b) => (
          <g key={b.bin_index}>
            {b.observed_sem > 0 && (
              <line
                x1={px(b.predicted_mean)}
                y1={py(b.observed_mean - b.observed_sem)}
                x2={px(b.predicted_mean)}
                y2={py(b.observed_mean + b.observed_sem)}
                stroke="#94a3b8"
                strokeWidth={1}
              />
            )}
            <circle cx={px(b.predicted_mean)} cy={py(b.observed_mean)} r={3} fill="#0369a1" />
          </g>
        ))}
        <text x={(M.left + W - M.right) / 2} y={H - 6} textAnchor="middle" className="fill-slate-500 text-[10px]">
          {curve.predicted_label}
        </text>
        <text
          x={12}
          y={(M.top + H - M.bottom) / 2}
          textAnchor="middle"
          transform={`rotate(-90 12 ${(M.top + H - M.bottom) / 2})`}
          className="fill-slate-500 text-[10px]"
        >
          {curve.observed_label}
        </text>
      </svg>

      <table className="mt-2 w-full text-[11px]">
        <thead>
          <tr className="text-left text-slate-400">
            <th className="font-medium">bin</th>
            <th className="font-medium">n</th>
            <th className="font-medium">predicted</th>
            <th className="font-medium">observed (± SEM)</th>
          </tr>
        </thead>
        <tbody>
          {curve.bins.map((b) => (
            <tr key={b.bin_index} className="border-t border-slate-100">
              <td className="text-slate-600">{b.bin_index}</td>
              <td className="text-slate-600">{b.n}</td>
              <td className="font-mono text-slate-800">{b.predicted_mean.toFixed(3)}</td>
              <td className="font-mono text-slate-800">
                {b.observed_mean.toFixed(3)} ± {b.observed_sem.toFixed(3)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className="mt-1 text-[11px] text-slate-500">
        {curve.n_bins} quantile bins over {curve.n} predictions. {curve.caveat}
      </p>
    </section>
  );
}
