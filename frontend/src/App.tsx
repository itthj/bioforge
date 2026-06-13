import { useEffect, useRef, useState } from "react";
import { AccuracyReport } from "./components/AccuracyReport";
import { ApprovalCard } from "./components/ApprovalCard";
import { ChatInput } from "./components/ChatInput";
import { FinalCard } from "./components/FinalCard";
import { MemoryInspector } from "./components/MemoryInspector";
import { ProjectSwitcher } from "./components/ProjectSwitcher";
import { RunDetail } from "./components/RunDetail";
import { RunHistory } from "./components/RunHistory";
import { TraceView } from "./components/TraceView";
import { FilesPanel } from "./components/FilesPanel";
import { PipelinesPanel } from "./components/PipelinesPanel";
import { UsageChip } from "./components/UsageChip";
import type { AuthContext } from "./components/AuthGate";
import { cancelRun, streamAgentApprove, streamAgentRun } from "./api/agent";
import { listProjects } from "./api/projects";
import { getTrace } from "./api/traces";
import { cn } from "./lib/cn";
import type {
  AgentDoneEvent,
  AgentStep,
  Autonomy,
  PlanPayload,
  SseEvent,
  ValidationVerdict,
} from "./types/agent";
import type { TraceDetail } from "./types/traces";

type RunState =
  | "idle"
  | "running"
  | "done"
  | "pending_approval"
  | "error"
  | "cancelled";
type Tab = "chat" | "history" | "memory" | "accuracy" | "data" | "pipelines";

const DEFAULT_PROJECT_ID = "default-project";

export function App({ auth }: { auth?: AuthContext } = {}) {
  const [projectId, setProjectId] = useState(DEFAULT_PROJECT_ID);
  const [tab, setTab] = useState<Tab>("chat");
  const [autonomy, setAutonomy] = useState<Autonomy>("auto");

  const [steps, setSteps] = useState<AgentStep[]>([]);
  const [done, setDone] = useState<AgentDoneEvent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [runState, setRunState] = useState<RunState>("idle");

  // Aborts the in-flight run (closes the SSE -> backend cancels the agent task).
  const abortRef = useRef<AbortController | null>(null);
  // The trace_id of an in-flight CELERY-backed run, learned from the `queued` event. Null for
  // inline runs (which carry no queued event) -- Stop then just aborts the stream as before.
  const celeryTraceIdRef = useRef<string | null>(null);
  // Last submitted goal, so the user can Retry after an error or a Stop.
  const [lastGoal, setLastGoal] = useState("");

  // History tab: the currently-opened past run (null = show the list), plus load errors.
  const [openedRun, setOpenedRun] = useState<TraceDetail | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  // Deep-link: ?run=<trace_id> opens that run on load (shareable permalink).
  useEffect(() => {
    const runId = new URLSearchParams(window.location.search).get("run");
    if (!runId) return;
    setTab("history");
    getTrace(runId)
      .then(setOpenedRun)
      .catch((e) => setRunError(e instanceof Error ? e.message : String(e)));
  }, []);

  // When signed in (auth on), land in one of YOUR OWN projects -- the global default-project
  // belongs to the default user and isn't yours. No-op in single-user mode (auth off).
  useEffect(() => {
    if (!auth?.user) return;
    let cancelled = false;
    listProjects()
      .then((ps) => {
        if (!cancelled && ps.length > 0) setProjectId(ps[0].id);
      })
      .catch(() => {
        /* leave the default selection; the switcher still lets them pick/create */
      });
    return () => {
      cancelled = true;
    };
  }, [auth?.user?.id]);

  async function consume(generator: AsyncGenerator<SseEvent>) {
    for await (const ev of generator) {
      if (ev.event === "queued") {
        // Celery-backed run: remember the trace_id so Stop can revoke the worker task.
        celeryTraceIdRef.current = ev.data.trace_id;
      } else if (ev.event === "step") {
        setSteps((prev) => [...prev, ev.data]);
      } else if (ev.event === "done") {
        celeryTraceIdRef.current = null; // terminal -- nothing left to cancel
        setDone(ev.data);
        if (ev.data.status === "pending_approval") {
          setRunState("pending_approval");
        } else if (
          ev.data.status === "error" ||
          ev.data.status === "critique_failed"
        ) {
          setRunState("error");
        } else {
          setRunState("done");
        }
      } else if (ev.event === "error") {
        setError(ev.data.message);
        setRunState("error");
      }
    }
  }

  async function handleSubmit(goal: string) {
    setLastGoal(goal);
    const controller = new AbortController();
    abortRef.current = controller;
    celeryTraceIdRef.current = null;
    setSteps([]);
    setDone(null);
    setError(null);
    setRunState("running");
    try {
      await consume(streamAgentRun({ goal, projectId, autonomy }, controller.signal));
    } catch (e: unknown) {
      if (controller.signal.aborted) {
        setRunState("cancelled"); // user pressed Stop — partial trace stays visible
      } else {
        setError(e instanceof Error ? e.message : String(e));
        setRunState("error");
      }
    } finally {
      abortRef.current = null;
    }
  }

  async function handleApproval(approved: boolean, editedPlan?: PlanPayload) {
    if (!done?.trace_id) return;
    const controller = new AbortController();
    abortRef.current = controller;
    setRunState("running");
    try {
      await consume(
        streamAgentApprove(
          { traceId: done.trace_id, approved, plan: editedPlan },
          controller.signal,
        ),
      );
    } catch (e: unknown) {
      if (controller.signal.aborted) {
        setRunState("cancelled");
      } else {
        setError(e instanceof Error ? e.message : String(e));
        setRunState("error");
      }
    } finally {
      abortRef.current = null;
    }
  }

  function handleStop() {
    // Closes the SSE; for an inline run the backend cancels the in-flight agent task on
    // disconnect. A celery run executes in a worker that a disconnect can't reach, so also
    // revoke it explicitly via /cancel (best-effort; errors are swallowed in cancelRun).
    abortRef.current?.abort();
    if (celeryTraceIdRef.current) {
      void cancelRun(celeryTraceIdRef.current);
      celeryTraceIdRef.current = null;
    }
  }

  function handleRetry() {
    if (lastGoal) handleSubmit(lastGoal);
  }

  function reset() {
    setSteps([]);
    setDone(null);
    setError(null);
    setRunState("idle");
  }

  function handleProjectChange(newProjectId: string) {
    if (newProjectId === projectId) return;
    setProjectId(newProjectId);
    // Switching projects clears the current run — its trace lives under the previous
    // project and the new one has its own context the planner will read.
    reset();
    setOpenedRun(null); // the opened run belongs to the previous project's history
  }

  function handleOpenRun(traceId: string) {
    setRunError(null);
    setTab("history");
    getTrace(traceId)
      .then((t) => {
        setOpenedRun(t);
        const url = new URL(window.location.href);
        url.searchParams.set("run", traceId);
        window.history.pushState({}, "", url);
      })
      .catch((e) => setRunError(e instanceof Error ? e.message : String(e)));
  }

  function handleBackToRuns() {
    setOpenedRun(null);
    setRunError(null);
    const url = new URL(window.location.href);
    url.searchParams.delete("run");
    window.history.pushState({}, "", url);
  }

  const inputDisabled = runState === "running" || runState === "pending_approval";
  // Switching projects mid-run is confusing; the active run is scoped to its
  // original project. Lock the switcher until the user resets.
  const switcherDisabled = inputDisabled;

  return (
    <div className="mx-auto max-w-3xl px-4 py-8">
      <header className="mb-4 flex items-center justify-between gap-4">
        <div className="flex items-center gap-2.5">
          <span className="inline-block h-2.5 w-2.5 rounded-full bg-accent" aria-hidden />
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-fg">BioForge</h1>
            <p className="text-xs text-fg-subtle">
              Agentic AI bioinformatics — type a goal, watch the agent reason.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {tab === "chat" && runState === "running" && (
            <button
              type="button"
              onClick={handleStop}
              className="rounded-md border border-danger bg-surface-2 px-3 py-1.5 text-xs font-medium text-danger shadow-sm transition-colors hover:bg-surface"
            >
              ■ Stop
            </button>
          )}
          {tab === "chat" && (runState === "error" || runState === "cancelled") && (
            <button
              type="button"
              onClick={handleRetry}
              disabled={!lastGoal}
              className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg shadow-sm transition hover:opacity-90 disabled:opacity-50"
            >
              ↻ Retry
            </button>
          )}
          {tab === "chat" && runState !== "idle" && runState !== "running" && (
            <button
              type="button"
              onClick={reset}
              className="rounded-md border border-border bg-surface-2 px-3 py-1.5 text-xs font-medium text-fg-muted shadow-sm transition-colors hover:text-fg"
            >
              New goal
            </button>
          )}
          <ProjectSwitcher
            currentProjectId={projectId}
            onChange={(p) => handleProjectChange(p.id)}
            disabled={switcherDisabled}
          />
          {auth?.user && (
            <div className="flex items-center gap-2 border-l border-border pl-2">
              <UsageChip />
              <span className="max-w-[14ch] truncate text-xs text-fg-subtle" title={auth.user.email}>
                {auth.user.display_name || auth.user.email}
              </span>
              <button
                type="button"
                onClick={() => void auth.logout()}
                className="rounded-md border border-border bg-surface-2 px-2 py-1 text-xs text-fg-subtle shadow-sm transition-colors hover:text-fg"
              >
                Sign out
              </button>
            </div>
          )}
        </div>
      </header>

      <nav className="mb-6 flex gap-1 border-b border-border">
        <TabButton active={tab === "chat"} onClick={() => setTab("chat")}>
          Chat
        </TabButton>
        <TabButton active={tab === "history"} onClick={() => setTab("history")}>
          History
        </TabButton>
        <TabButton active={tab === "data"} onClick={() => setTab("data")}>
          Data
        </TabButton>
        <TabButton active={tab === "memory"} onClick={() => setTab("memory")}>
          Memory
        </TabButton>
        <TabButton active={tab === "accuracy"} onClick={() => setTab("accuracy")}>
          Accuracy
        </TabButton>
        <TabButton active={tab === "pipelines"} onClick={() => setTab("pipelines")}>
          Pipelines
        </TabButton>
      </nav>

      {tab === "chat" && (
        <ChatPanel
          steps={steps}
          done={done}
          error={error}
          inputDisabled={inputDisabled}
          runState={runState}
          autonomy={autonomy}
          onAutonomyChange={setAutonomy}
          onSubmit={handleSubmit}
          onApproval={handleApproval}
        />
      )}
      {tab === "history" &&
        (openedRun ? (
          <RunDetail trace={openedRun} onBack={handleBackToRuns} />
        ) : (
          <>
            {runError && (
              <div className="mb-3 rounded-md border border-danger bg-surface p-3 text-sm text-danger">
                {runError}
              </div>
            )}
            <RunHistory projectId={projectId} onOpen={handleOpenRun} />
          </>
        ))}
      {tab === "data" && <FilesPanel projectId={projectId} />}
      {tab === "pipelines" && <PipelinesPanel projectId={projectId} />}
      {tab === "memory" && <MemoryInspector projectId={projectId} />}
      {tab === "accuracy" && <AccuracyReport />}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors ${
        active
          ? "border-accent text-fg"
          : "border-transparent text-fg-subtle hover:text-fg-muted"
      }`}
    >
      {children}
    </button>
  );
}

function ChatPanel({
  steps,
  done,
  error,
  inputDisabled,
  runState,
  autonomy,
  onAutonomyChange,
  onSubmit,
  onApproval,
}: {
  steps: AgentStep[];
  done: AgentDoneEvent | null;
  error: string | null;
  inputDisabled: boolean;
  runState: RunState;
  autonomy: Autonomy;
  onAutonomyChange: (a: Autonomy) => void;
  onSubmit: (goal: string) => void;
  onApproval: (approved: boolean, editedPlan?: PlanPayload) => void;
}) {
  // The grounding verdict (with per-claim offsets + provenance) rides on the validation
  // step; FinalCard uses it to highlight grounded/flagged values inline.
  const grounding = steps.find((s) => s.type === "validation")?.verdict as
    | ValidationVerdict
    | undefined;
  return (
    <>
      <div className="mb-3">
        <AutonomyToggle
          value={autonomy}
          onChange={onAutonomyChange}
          disabled={inputDisabled}
        />
      </div>
      <ChatInput onSubmit={onSubmit} disabled={inputDisabled} />

      {error && (
        <div className="mt-4 rounded-md border border-danger bg-surface p-3 text-sm text-danger">
          {error}
        </div>
      )}

      {runState === "cancelled" && (
        <div className="mt-4 rounded-md border border-border bg-surface-2 p-3 text-sm text-fg-muted">
          Run stopped. The partial trace below is what finished before you stopped it —
          press <span className="font-medium text-fg">Retry</span> to run the goal again.
        </div>
      )}

      {(steps.length > 0 || runState === "running") && (
        <section className="mt-6 space-y-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-fg-subtle">
            Trace
          </h2>
          <TraceView steps={steps} live={runState === "running"} />
        </section>
      )}

      {done && (
        <section className="mt-6 space-y-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-fg-subtle">
            {done.status === "pending_approval" ? "Approval" : "Result"}
          </h2>
          {done.status === "pending_approval" ? (
            <ApprovalCard
              key={done.trace_id}
              done={done}
              onDecision={onApproval}
              disabled={runState === "running"}
            />
          ) : (
            <FinalCard done={done} grounding={grounding} />
          )}
        </section>
      )}
    </>
  );
}

const AUTONOMY_OPTIONS: { key: Autonomy; label: string; hint: string }[] = [
  {
    key: "auto",
    label: "Auto",
    hint: "Runs automatically; pauses only for costly or destructive steps.",
  },
  {
    key: "review",
    label: "Review plan",
    hint: "Pauses after planning so you approve the plan before any tool runs.",
  },
];

function AutonomyToggle({
  value,
  onChange,
  disabled,
}: {
  value: Autonomy;
  onChange: (a: Autonomy) => void;
  disabled?: boolean;
}) {
  const active = AUTONOMY_OPTIONS.find((o) => o.key === value) ?? AUTONOMY_OPTIONS[0];
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-fg-subtle">
          Autonomy
        </span>
        <div
          role="group"
          aria-label="Autonomy level"
          className="inline-flex rounded-md border border-border bg-surface p-0.5"
        >
          {AUTONOMY_OPTIONS.map((o) => {
            const isActive = o.key === value;
            return (
              <button
                key={o.key}
                type="button"
                disabled={disabled}
                aria-pressed={isActive}
                title={o.hint}
                onClick={() => onChange(o.key)}
                className={cn(
                  "rounded px-2.5 py-1 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
                  isActive ? "bg-surface-2 text-fg" : "text-fg-subtle hover:text-fg-muted",
                )}
              >
                {o.label}
              </button>
            );
          })}
        </div>
      </div>
      <span className="text-[11px] text-fg-subtle">{active.hint}</span>
    </div>
  );
}
