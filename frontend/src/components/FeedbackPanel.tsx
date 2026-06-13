import { useEffect, useMemo, useState } from "react";
import {
  type AgreementResponse,
  type Prediction,
  getAgreement,
  listPredictions,
  recordOutcome,
  recordPredictions,
} from "../api/predictions";
import { ReliabilityDiagram } from "./ReliabilityDiagram";
import { CalibrationDiagram } from "./CalibrationDiagram";

/**
 * The wet-lab feedback loop (Limitation #4): record a platform PREDICTION, record the measured
 * OUTCOME after the experiment, then recompute agreement / calibration over the matched pairs.
 * Reuses ReliabilityDiagram (regression) and CalibrationDiagram (probability). Honest by
 * construction: only predictions with an outcome feed the curve, and n_matched / n_pending show.
 */
interface Props {
  projectId: string;
}

export function FeedbackPanel({ projectId }: Props) {
  const [predictions, setPredictions] = useState<Prediction[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [agreement, setAgreement] = useState<AgreementResponse | null>(null);
  const [selectedAssay, setSelectedAssay] = useState<string>("");

  // New-prediction form.
  const [subjectKey, setSubjectKey] = useState("");
  const [assay, setAssay] = useState("");
  const [predictedValue, setPredictedValue] = useState("");
  const [kind, setKind] = useState<"regression" | "probability">("regression");

  // Per-row outcome entry.
  const [outcomeDrafts, setOutcomeDrafts] = useState<Record<string, string>>({});

  useEffect(() => {
    load();
  }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function load() {
    try {
      setPredictions(await listPredictions(projectId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load predictions");
    }
  }

  const assays = useMemo(() => Array.from(new Set(predictions.map((p) => p.assay))).sort(), [predictions]);

  async function handleAddPrediction(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const value = Number(predictedValue);
    if (!subjectKey.trim() || !assay.trim() || Number.isNaN(value)) {
      setError("subject key, assay, and a numeric predicted value are required.");
      return;
    }
    try {
      await recordPredictions(projectId, [
        { subject_key: subjectKey.trim(), assay: assay.trim(), predicted_value: value, kind },
      ]);
      setSubjectKey("");
      setPredictedValue("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to record prediction");
    }
  }

  async function handleRecordOutcome(p: Prediction) {
    const raw = outcomeDrafts[p.id];
    const value = Number(raw);
    if (raw === undefined || raw === "" || Number.isNaN(value)) {
      setError("Enter a numeric measured value.");
      return;
    }
    setError(null);
    try {
      await recordOutcome(p.id, value);
      setOutcomeDrafts((d) => ({ ...d, [p.id]: "" }));
      await load();
      if (selectedAssay === p.assay) await refreshAgreement(p.assay);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to record outcome");
    }
  }

  async function refreshAgreement(a: string) {
    setSelectedAssay(a);
    setError(null);
    try {
      setAgreement(await getAgreement(projectId, a));
    } catch (e) {
      setAgreement(null);
      setError(e instanceof Error ? e.message : "Failed to compute agreement");
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <header>
        <h2 className="text-sm font-semibold text-fg">Wet-lab feedback loop</h2>
        <p className="text-[11px] text-fg-subtle">
          Record a prediction, run the experiment, record the measured outcome — then recompute how
          well the platform agreed with your own results. Only matched pairs feed the curve.
        </p>
      </header>

      {error && <div className="rounded border border-danger bg-surface p-2 text-xs text-danger">{error}</div>}

      {/* Add prediction */}
      <form onSubmit={handleAddPrediction} className="flex flex-wrap items-end gap-2 rounded border border-border p-3">
        <Field label="subject key">
          <input className={inputCls} value={subjectKey} onChange={(e) => setSubjectKey(e.target.value)} placeholder="GUIDE_A" />
        </Field>
        <Field label="assay">
          <input className={inputCls} value={assay} onChange={(e) => setAssay(e.target.value)} placeholder="on-target eff" />
        </Field>
        <Field label="predicted">
          <input className={`${inputCls} w-24`} value={predictedValue} onChange={(e) => setPredictedValue(e.target.value)} placeholder="0.80" />
        </Field>
        <Field label="kind">
          <select className={inputCls} value={kind} onChange={(e) => setKind(e.target.value as "regression" | "probability")}>
            <option value="regression">regression</option>
            <option value="probability">probability (0/1)</option>
          </select>
        </Field>
        <button type="submit" className="rounded bg-accent px-3 py-1.5 text-xs font-medium text-white hover:opacity-90">
          Record prediction
        </button>
      </form>

      {/* Predictions table */}
      <div className="overflow-x-auto rounded border border-border">
        <table className="w-full text-left text-xs">
          <thead className="text-fg-subtle">
            <tr>
              <th className="px-2 py-1 font-medium">subject</th>
              <th className="px-2 py-1 font-medium">assay</th>
              <th className="px-2 py-1 font-medium">kind</th>
              <th className="px-2 py-1 font-medium">predicted</th>
              <th className="px-2 py-1 font-medium">measured outcome</th>
            </tr>
          </thead>
          <tbody className="font-mono text-fg">
            {predictions.length === 0 && (
              <tr>
                <td colSpan={5} className="px-2 py-3 text-center font-sans text-fg-subtle">
                  No predictions yet.
                </td>
              </tr>
            )}
            {predictions.map((p) => (
              <tr key={p.id} className="border-t border-border">
                <td className="px-2 py-1">{p.subject_key}</td>
                <td className="px-2 py-1">{p.assay}</td>
                <td className="px-2 py-1 text-fg-subtle">{p.kind}</td>
                <td className="px-2 py-1">{p.predicted_value}</td>
                <td className="px-2 py-1">
                  {p.observed_value !== null ? (
                    <span className="text-success">{p.observed_value}</span>
                  ) : (
                    <span className="flex items-center gap-1">
                      <input
                        className={`${inputCls} w-20`}
                        value={outcomeDrafts[p.id] ?? ""}
                        onChange={(e) => setOutcomeDrafts((d) => ({ ...d, [p.id]: e.target.value }))}
                        placeholder={p.kind === "probability" ? "0 or 1" : "0.65"}
                      />
                      <button
                        className="rounded border border-border px-2 py-1 font-sans text-[11px] hover:bg-surface-2"
                        onClick={() => handleRecordOutcome(p)}
                      >
                        save
                      </button>
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Agreement */}
      {assays.length > 0 && (
        <div className="flex flex-col gap-3 rounded border border-border p-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium text-fg">Recompute agreement for assay:</span>
            {assays.map((a) => (
              <button
                key={a}
                onClick={() => refreshAgreement(a)}
                className={`rounded px-2 py-1 text-[11px] ${
                  selectedAssay === a ? "bg-accent text-white" : "border border-border hover:bg-surface-2"
                }`}
              >
                {a}
              </button>
            ))}
          </div>

          {agreement && (
            <div className="flex flex-col gap-2">
              <div className="text-[11px] text-fg-subtle">
                {agreement.n_matched} of {agreement.n_total} predictions have a measured outcome
                {agreement.n_pending > 0 ? ` · ${agreement.n_pending} pending` : ""}.
              </div>
              {agreement.reliability && <ReliabilityDiagram curve={agreement.reliability} />}
              {agreement.calibration && <CalibrationDiagram curve={agreement.calibration} />}
              {!agreement.reliability && !agreement.calibration && (
                <p className="text-[11px] italic text-warn">
                  Need at least 2 measured outcomes to draw a curve — record more results to close the loop.
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const inputCls = "rounded border border-border bg-surface px-2 py-1 text-xs text-fg";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-0.5">
      <span className="text-[10px] font-medium uppercase tracking-wide text-fg-subtle">{label}</span>
      {children}
    </label>
  );
}
