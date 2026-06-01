import type { ScoreGuideOnTargetOutput } from "../types/on_target";

interface OnTargetScoreCardProps {
  output: ScoreGuideOnTargetOutput;
}

interface Scorer {
  key: string;
  label: string;
  value: number;
  version: string | null;
  primary: boolean;
}

/**
 * Renders score_guide_on_target's output: the rule-based proxy plus any opt-in deep scorers
 * (DeepCRISPR, Doench Rule Set 2 / Azimuth) SIDE BY SIDE, with the honest uncertainty framing
 * the blueprint requires (rule 10 / §6): these models emit point estimates, not calibrated
 * per-guide intervals, and different scorers use different scales — so a disagreement is itself
 * a signal, not noise. We render only what the backend actually returned; nothing is fabricated.
 */
export function OnTargetScoreCard({ output }: OnTargetScoreCardProps) {
  const scorers: Scorer[] = [
    {
      key: "rule_based",
      label: "Rule-based (Doench 2014/2016)",
      value: output.on_target_score,
      version: "transparent proxy",
      primary: true,
    },
  ];
  if (output.deepcrispr_on_target_score !== null) {
    scorers.push({
      key: "deepcrispr",
      label: "DeepCRISPR (Chuai 2018)",
      value: output.deepcrispr_on_target_score,
      version: output.deepcrispr_model_version,
      primary: false,
    });
  }
  if (output.azimuth_rs2_on_target_score !== null) {
    scorers.push({
      key: "azimuth_rs2",
      label: "Doench Rule Set 2 (Azimuth)",
      value: output.azimuth_rs2_on_target_score,
      version: output.azimuth_rs2_model_version,
      primary: false,
    });
  }
  const multiple = scorers.length > 1;

  return (
    <div className="space-y-3 rounded-md border border-emerald-200 bg-white p-3 shadow-sm">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="text-xs font-semibold uppercase tracking-wider text-emerald-700">
          On-target score
        </div>
        <div className="font-mono text-xs text-slate-500">
          {output.protospacer}
          {output.pam ? ` + ${output.pam}` : ""}
        </div>
      </header>

      <div className={`grid gap-2 ${multiple ? "sm:grid-cols-2" : ""}`}>
        {scorers.map((s) => (
          <div
            key={s.key}
            className={`rounded border px-2 py-1.5 ${
              s.primary
                ? "border-emerald-300 bg-emerald-50/50"
                : "border-slate-200 bg-slate-50"
            }`}
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-[10px] font-medium uppercase tracking-wider text-slate-500">
                {s.label}
              </span>
              {!s.primary && (
                <span className="rounded bg-slate-200 px-1 text-[9px] font-medium text-slate-600">
                  secondary
                </span>
              )}
            </div>
            <div className="font-mono text-lg text-slate-900">{s.value.toFixed(3)}</div>
            {s.version && (
              <div
                className="truncate font-mono text-[10px] text-slate-400"
                title={s.version}
              >
                {s.version}
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="rounded border border-sky-200 bg-sky-50 p-2 text-[11px] text-sky-900">
        Point estimate{multiple ? "s" : ""} —{" "}
        {multiple ? "these scorers emit" : "this scorer emits"} a single number, not a
        calibrated per-guide confidence interval.{" "}
        {multiple
          ? "Different scorers use different scales, so compare guide RANKINGS, not absolute values; a strong disagreement between them is itself a signal of elevated uncertainty."
          : "Model-level published accuracy (not a per-guide interval) is the honest confidence measure here."}
      </div>

      {output.caveats.length > 0 && (
        <div className="rounded border border-amber-200 bg-amber-50 p-2 text-[11px] text-amber-900">
          <div className="mb-1 font-semibold">Caveats</div>
          <ul className="ml-4 list-disc space-y-1">
            {output.caveats.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
