import type {
  CrisprEditReportOutput,
  GuideReport,
  RecommendationLabel,
} from "../types/crispr";
import { IgvGuideViewer } from "./IgvGuideViewer";

interface CrisprReportCardProps {
  report: CrisprEditReportOutput;
}

const LABEL_STYLES: Record<RecommendationLabel, string> = {
  preferred: "bg-emerald-100 text-emerald-800 border-emerald-300",
  acceptable: "bg-sky-100 text-sky-800 border-sky-300",
  caution: "bg-amber-100 text-amber-800 border-amber-300",
  avoid: "bg-rose-100 text-rose-800 border-rose-300",
};

export function CrisprReportCard({ report }: CrisprReportCardProps) {
  return (
    <div className="space-y-3 rounded-md border border-emerald-200 bg-white p-3 shadow-sm">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-emerald-700">
            CRISPR edit report
          </div>
          <div className="font-mono text-xs text-slate-500">
            target {report.target_length} nt · PAM {report.pam} ·{" "}
            {report.num_guides_considered} candidates · tools{" "}
            {report.tool_chain.join(" → ")}
          </div>
        </div>
      </header>

      {report.guides.length > 0 && report.target_sequence && (
        <IgvGuideViewer report={report} />
      )}

      {report.recommended_guide ? (
        <RecommendedGuide guide={report.recommended_guide} />
      ) : (
        <div className="rounded-md border border-dashed border-slate-300 bg-slate-50 p-3 text-xs text-slate-600">
          No guide met the recommendation criteria. See per-guide rationales below.
        </div>
      )}

      {report.guides.length > 0 && (
        <details>
          <summary className="cursor-pointer text-xs font-medium text-slate-700 hover:text-slate-900">
            All {report.guides.length} candidate guides
          </summary>
          <ol className="mt-2 space-y-2">
            {report.guides.map((guide) => (
              <li key={`${guide.rank}-${guide.protospacer}`}>
                <GuideRow guide={guide} dense />
              </li>
            ))}
          </ol>
        </details>
      )}

      {report.caveats.length > 0 && (
        <div className="rounded border border-amber-200 bg-amber-50 p-2 text-[11px] text-amber-900">
          <div className="mb-1 font-semibold">Caveats</div>
          <ul className="ml-4 list-disc space-y-1">
            {report.caveats.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function RecommendedGuide({ guide }: { guide: GuideReport }) {
  return (
    <div className="rounded-md border-2 border-emerald-300 bg-emerald-50/60 p-3">
      <div className="mb-2 flex items-center gap-2 text-xs">
        <span className="font-semibold text-emerald-800">Recommended</span>
        <span className="text-slate-400">rank #{guide.rank}</span>
      </div>
      <GuideRow guide={guide} />
    </div>
  );
}

function GuideRow({ guide, dense = false }: { guide: GuideReport; dense?: boolean }) {
  return (
    <div className={`${dense ? "" : "space-y-2"} rounded ${dense ? "border border-slate-200 bg-white p-2" : ""}`}>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-mono font-semibold text-slate-900">
          {guide.protospacer}
        </span>
        <span className="font-mono text-slate-500">
          + {guide.pam_sequence}
        </span>
        <span
          className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-medium ${LABEL_STYLES[guide.recommendation_label]}`}
        >
          {guide.recommendation_label}
        </span>
        <span className="font-mono text-[11px] text-slate-500">
          strand {guide.strand} · pos {guide.protospacer_start}-{guide.protospacer_end}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-2 text-[11px]">
        <Metric label="recommendation" value={guide.recommendation_score.toFixed(3)} />
        <Metric
          label="on-target"
          value={
            guide.on_target_score === null
              ? "—"
              : guide.on_target_score.toFixed(3)
          }
          hint={guide.on_target_score === null ? "not computed" : undefined}
        />
        <Metric label="heuristic" value={guide.heuristic_score.toFixed(3)} />
      </div>

      {guide.edit_outcome_summary && (
        <div className="text-[11px] text-slate-600">
          <span className="font-medium">Edit outcomes</span> @ cut{" "}
          {guide.edit_outcome_summary.cut_position_fwd}: frameshift{" "}
          {(guide.edit_outcome_summary.frameshift_probability * 100).toFixed(0)}% ·
          no-edit{" "}
          {(guide.edit_outcome_summary.no_edit_probability * 100).toFixed(0)}%
        </div>
      )}

      {guide.off_target_summary.searched && (
        <div className="text-[11px] text-slate-600">
          <span className="font-medium">Off-target</span> ({guide.off_target_summary.database}):{" "}
          <span className="text-rose-700">{guide.off_target_summary.high_risk_count} high</span>
          {" · "}
          <span className="text-amber-700">{guide.off_target_summary.medium_risk_count} medium</span>
          {" · "}
          <span className="text-slate-500">{guide.off_target_summary.low_risk_count} low</span>
        </div>
      )}

      {guide.rationale.length > 0 && (
        <ul className="ml-4 list-disc text-[11px] text-slate-600">
          {guide.rationale.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded bg-slate-50 px-2 py-1">
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className="font-mono text-sm text-slate-900">{value}</div>
      {hint && <div className="text-[10px] italic text-slate-400">{hint}</div>}
    </div>
  );
}
