import type { ScoreGuideOnTargetOutput } from "../types/on_target";
import { downloadBlob, toCsv } from "../lib/download";
import { ExportButton } from "./ui/ExportButton";

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

  function exportCsv() {
    const header = ["scorer", "label", "score", "version"];
    const rows = scorers.map((s) => [s.key, s.label, s.value, s.version]);
    const meta = [["protospacer", output.protospacer], ["pam", output.pam]];
    downloadBlob(
      "on_target_scores.csv",
      "text/csv;charset=utf-8",
      toCsv([...meta, [], header, ...rows]),
    );
  }

  return (
    <div className="space-y-3 rounded-md border border-border bg-surface p-3 shadow-sm">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="text-xs font-semibold uppercase tracking-wider text-success">
          On-target score
        </div>
        <div className="flex items-baseline gap-3">
          <span className="font-mono text-xs text-fg-subtle">
            {output.protospacer}
            {output.pam ? ` + ${output.pam}` : ""}
          </span>
          <ExportButton
            label="CSV"
            title="Download the on-target scores as CSV"
            onClick={exportCsv}
          />
        </div>
      </header>

      <div className={`grid gap-2 ${multiple ? "sm:grid-cols-2" : ""}`}>
        {scorers.map((s) => (
          <div
            key={s.key}
            className={`rounded border px-2 py-1.5 ${
              s.primary
                ? "border-border bg-surface-2/50"
                : "border-border bg-bg"
            }`}
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-[10px] font-medium uppercase tracking-wider text-fg-subtle">
                {s.label}
              </span>
              {!s.primary && (
                <span className="rounded bg-surface-2 px-1 text-[9px] font-medium text-fg-muted">
                  secondary
                </span>
              )}
            </div>
            <div className="font-mono text-lg text-fg">{s.value.toFixed(3)}</div>
            {s.version && (
              <div
                className="truncate font-mono text-[10px] text-fg-subtle"
                title={s.version}
              >
                {s.version}
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="rounded border border-border bg-surface-2 p-2 text-[11px] text-accent">
        Point estimate{multiple ? "s" : ""} —{" "}
        {multiple ? "these scorers emit" : "this scorer emits"} a single number, not a
        calibrated per-guide confidence interval.{" "}
        {multiple
          ? "Different scorers use different scales, so compare guide RANKINGS, not absolute values; a strong disagreement between them is itself a signal of elevated uncertainty."
          : "Model-level published accuracy (not a per-guide interval) is the honest confidence measure here."}
      </div>

      {output.caveats.length > 0 && (
        <div className="rounded border border-border bg-surface-2 p-2 text-[11px] text-warn">
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
