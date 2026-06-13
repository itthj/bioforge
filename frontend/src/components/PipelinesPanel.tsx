import { useEffect, useRef, useState } from "react";
import {
  type PipelineEvent,
  type PipelineJob,
  cancelPipeline,
  fetchSupportedPipelines,
  listPipelines,
  submitPipeline,
} from "../api/pipelines";

// Default samplesheet templates keyed by pipeline, so users can see the expected format.
const SAMPLESHEET_TEMPLATES: Record<string, string> = {
  "nf-core/rnaseq":
    "sample,fastq_1,fastq_2,strandedness\nCONTROL_REP1,path/to/control_R1.fastq.gz,path/to/control_R2.fastq.gz,auto\n",
  "nf-core/sarek":
    "patient,sex,status,sample,lane,fastq_1,fastq_2\nPATIENT1,XX,0,SAMPLE1,lane1,path/to/R1.fastq.gz,path/to/R2.fastq.gz\n",
  "nf-core/atacseq":
    "sample,fastq_1,fastq_2,replicate\nCONTROL1,path/to/control_R1.fastq.gz,path/to/control_R2.fastq.gz,1\n",
  "nf-core/ampliseq":
    "sampleID,forwardReads,reverseReads\nSAMPLE1,path/to/R1.fastq.gz,path/to/R2.fastq.gz\n",
};

const STATUS_BADGE: Record<string, string> = {
  queued: "bg-yellow-100 text-yellow-800",
  running: "bg-blue-100 text-blue-800",
  completed: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
  cancelled: "bg-gray-100 text-gray-600",
};

function eventLabel(ev: PipelineEvent): string {
  switch (ev.type) {
    case "run_started":
      return "Pipeline started";
    case "run_completed":
      return "Pipeline completed";
    case "run_failed":
      return `Failed: ${(ev.payload as Record<string, string>)?.error ?? "unknown error"}`;
    case "step_started":
      return `Step started: ${ev.step_name ?? ""}`;
    case "step_completed":
      return `Step completed: ${ev.step_name ?? ""}`;
    case "step_failed":
      return `Step failed: ${ev.step_name ?? ""} (exit ${(ev.payload as Record<string, string>)?.exit ?? "?"})`;
    default:
      return ev.type;
  }
}

interface Props {
  projectId: string;
}

export function PipelinesPanel({ projectId }: Props) {
  const [jobs, setJobs] = useState<PipelineJob[]>([]);
  const [catalogue, setCatalogue] = useState<Record<string, string>>({});
  const [selectedJob, setSelectedJob] = useState<PipelineJob | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const streamRef = useRef<EventSource | null>(null);

  // Form state
  const [pipeline, setPipeline] = useState("nf-core/rnaseq");
  const [profile, setProfile] = useState("test");
  const [samplesheet, setSamplesheet] = useState(SAMPLESHEET_TEMPLATES["nf-core/rnaseq"] ?? "");

  useEffect(() => {
    fetchSupportedPipelines().then(setCatalogue).catch(() => {});
    loadJobs();
  }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function loadJobs() {
    try {
      const data = await listPipelines(projectId);
      setJobs(data);
      if (selectedJob) {
        const refreshed = data.find((j) => j.id === selectedJob.id);
        if (refreshed) setSelectedJob(refreshed);
      }
    } catch {
      // non-fatal; job list stays stale
    }
  }

  function openStream(jobId: string) {
    if (streamRef.current) streamRef.current.close();
    const es = new EventSource(`/pipelines/${jobId}/stream`);
    streamRef.current = es;
    es.addEventListener("event", (e) => {
      const ev: PipelineEvent = JSON.parse(e.data);
      setSelectedJob((prev) => {
        if (!prev || prev.id !== jobId) return prev;
        const exists = prev.events.some((x) => x.seq === ev.seq);
        if (exists) return prev;
        return { ...prev, events: [...prev.events, ev] };
      });
    });
    es.addEventListener("done", (e) => {
      const data = JSON.parse(e.data) as { status: string; error: string | null };
      es.close();
      setSelectedJob((prev) => (prev && prev.id === jobId ? { ...prev, ...data } : prev));
      loadJobs();
    });
    es.addEventListener("error", () => {
      es.close();
    });
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const job = await submitPipeline({
        project_id: projectId,
        pipeline,
        profile,
        samplesheet,
      });
      setJobs((prev) => [job, ...prev]);
      setSelectedJob(job);
      setShowForm(false);
      openStream(job.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Submit failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleCancel(jobId: string) {
    await cancelPipeline(jobId);
    loadJobs();
    if (selectedJob?.id === jobId) {
      setSelectedJob((prev) => (prev ? { ...prev, status: "cancelled" } : prev));
    }
  }

  function handlePipelineChange(p: string) {
    setPipeline(p);
    setSamplesheet(SAMPLESHEET_TEMPLATES[p] ?? "");
  }

  return (
    <div className="flex h-full gap-4">
      {/* Left: job list */}
      <div className="w-64 shrink-0 flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <span className="font-semibold text-sm">Pipeline runs</span>
          <button
            className="text-xs px-2 py-1 rounded bg-indigo-600 text-white hover:bg-indigo-700"
            onClick={() => setShowForm((v) => !v)}
          >
            + New run
          </button>
        </div>
        <div className="flex flex-col gap-1 overflow-y-auto">
          {jobs.length === 0 && <p className="text-xs text-gray-500 italic">No pipeline runs yet.</p>}
          {jobs.map((j) => (
            <button
              key={j.id}
              className={`text-left rounded p-2 text-xs border transition-colors ${
                selectedJob?.id === j.id
                  ? "border-indigo-400 bg-indigo-50"
                  : "border-gray-200 hover:border-gray-300 hover:bg-gray-50"
              }`}
              onClick={() => {
                setSelectedJob(j);
                if (j.status === "queued" || j.status === "running") openStream(j.id);
              }}
            >
              <div className="font-mono truncate">{j.pipeline.replace("nf-core/", "")}</div>
              <div className="flex items-center gap-1 mt-0.5">
                <span className={`px-1 rounded text-[10px] font-medium ${STATUS_BADGE[j.status] ?? "bg-gray-100"}`}>
                  {j.status}
                </span>
                <span className="text-gray-400">{j.revision}</span>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Right: form or detail */}
      <div className="flex-1 overflow-y-auto">
        {showForm && (
          <form onSubmit={handleSubmit} className="flex flex-col gap-3 max-w-lg">
            <h3 className="font-semibold text-sm">Submit nf-core pipeline</h3>

            <div>
              <label className="text-xs font-medium text-gray-700">Pipeline</label>
              <select
                className="mt-1 block w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
                value={pipeline}
                onChange={(e) => handlePipelineChange(e.target.value)}
              >
                {Object.entries(catalogue).length > 0
                  ? Object.entries(catalogue).map(([k, v]) => (
                      <option key={k} value={k}>
                        {k} (default {v})
                      </option>
                    ))
                  : Object.keys(SAMPLESHEET_TEMPLATES).map((k) => (
                      <option key={k} value={k}>
                        {k}
                      </option>
                    ))}
              </select>
            </div>

            <div>
              <label className="text-xs font-medium text-gray-700">
                Profile
                <span className="ml-1 font-normal text-gray-500">(comma-separated nextflow profiles)</span>
              </label>
              <input
                className="mt-1 block w-full rounded border border-gray-300 px-2 py-1.5 text-sm font-mono"
                value={profile}
                onChange={(e) => setProfile(e.target.value)}
                placeholder="test,docker"
              />
            </div>

            <div>
              <label className="text-xs font-medium text-gray-700">
                Samplesheet
                <span className="ml-1 font-normal text-gray-500">(CSV — see nf-core docs for format)</span>
              </label>
              <textarea
                className="mt-1 block w-full rounded border border-gray-300 px-2 py-1.5 text-xs font-mono h-36 resize-y"
                value={samplesheet}
                onChange={(e) => setSamplesheet(e.target.value)}
                spellCheck={false}
              />
            </div>

            {error && <p className="text-xs text-red-600">{error}</p>}

            <div className="flex gap-2">
              <button
                type="submit"
                disabled={loading || !samplesheet.trim()}
                className="px-3 py-1.5 rounded bg-indigo-600 text-white text-sm hover:bg-indigo-700 disabled:opacity-50"
              >
                {loading ? "Submitting…" : "Submit"}
              </button>
              <button
                type="button"
                className="px-3 py-1.5 rounded border border-gray-300 text-sm hover:bg-gray-50"
                onClick={() => setShowForm(false)}
              >
                Cancel
              </button>
            </div>

            <p className="text-[11px] text-gray-500">
              Requires <code>BIOFORGE_NEXTFLOW_ENABLED=true</code> and Nextflow on PATH. Use profile{" "}
              <code>test</code> for a fast smoke test without real data.
            </p>
          </form>
        )}

        {!showForm && selectedJob && (
          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-3">
              <div>
                <div className="font-semibold text-sm">{selectedJob.pipeline}</div>
                <div className="text-xs text-gray-500">
                  revision {selectedJob.revision} · profile {selectedJob.profile}
                </div>
              </div>
              <span
                className={`ml-auto px-2 py-0.5 rounded text-xs font-medium ${STATUS_BADGE[selectedJob.status] ?? "bg-gray-100"}`}
              >
                {selectedJob.status}
              </span>
              {(selectedJob.status === "queued" || selectedJob.status === "running") && (
                <button
                  className="text-xs text-red-600 hover:underline"
                  onClick={() => handleCancel(selectedJob.id)}
                >
                  Cancel
                </button>
              )}
            </div>

            {selectedJob.error && (
              <div className="rounded bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700 font-mono whitespace-pre-wrap">
                {selectedJob.error}
              </div>
            )}

            <div className="flex flex-col gap-1">
              <span className="text-xs font-medium text-gray-700">Event log</span>
              {selectedJob.events.length === 0 ? (
                <p className="text-xs text-gray-400 italic">Waiting for events…</p>
              ) : (
                <div className="rounded border border-gray-200 divide-y divide-gray-100">
                  {selectedJob.events.map((ev) => (
                    <div key={ev.seq} className="px-3 py-1.5 text-xs flex items-start gap-2">
                      <span className="text-gray-400 shrink-0 font-mono">
                        {new Date(ev.ts).toLocaleTimeString()}
                      </span>
                      <span
                        className={
                          ev.type.includes("failed")
                            ? "text-red-700"
                            : ev.type.includes("completed") || ev.type === "run_completed"
                              ? "text-green-700"
                              : "text-gray-700"
                        }
                      >
                        {eventLabel(ev)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {!showForm && !selectedJob && (
          <div className="flex items-center justify-center h-40 text-sm text-gray-400">
            Select a run or click &quot;+ New run&quot; to submit a pipeline.
          </div>
        )}
      </div>
    </div>
  );
}
