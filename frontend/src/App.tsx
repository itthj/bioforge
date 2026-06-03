import { useState } from "react";
import { AccuracyReport } from "./components/AccuracyReport";
import { ApprovalCard } from "./components/ApprovalCard";
import { ChatInput } from "./components/ChatInput";
import { FinalCard } from "./components/FinalCard";
import { MemoryInspector } from "./components/MemoryInspector";
import { ProjectSwitcher } from "./components/ProjectSwitcher";
import { TraceView } from "./components/TraceView";
import { streamAgentApprove, streamAgentRun } from "./api/agent";
import { cn } from "./lib/cn";
import type {
  AgentDoneEvent,
  AgentStep,
  Autonomy,
  SseEvent,
  ValidationVerdict,
} from "./types/agent";

type RunState = "idle" | "running" | "done" | "pending_approval" | "error";
type Tab = "chat" | "memory" | "accuracy";

const DEFAULT_PROJECT_ID = "default-project";

export function App() {
  const [projectId, setProjectId] = useState(DEFAULT_PROJECT_ID);
  const [tab, setTab] = useState<Tab>("chat");
  const [autonomy, setAutonomy] = useState<Autonomy>("auto");

  const [steps, setSteps] = useState<AgentStep[]>([]);
  const [done, setDone] = useState<AgentDoneEvent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [runState, setRunState] = useState<RunState>("idle");

  async function consume(generator: AsyncGenerator<SseEvent>) {
    for await (const ev of generator) {
      if (ev.event === "step") {
        setSteps((prev) => [...prev, ev.data]);
      } else if (ev.event === "done") {
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
    setSteps([]);
    setDone(null);
    setError(null);
    setRunState("running");
    try {
      await consume(streamAgentRun({ goal, projectId, autonomy }));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setRunState("error");
    }
  }

  async function handleApproval(approved: boolean) {
    if (!done?.trace_id) return;
    setRunState("running");
    try {
      await consume(streamAgentApprove({ traceId: done.trace_id, approved }));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setRunState("error");
    }
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
          {runState !== "idle" && tab === "chat" && (
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
        </div>
      </header>

      <nav className="mb-6 flex gap-1 border-b border-border">
        <TabButton active={tab === "chat"} onClick={() => setTab("chat")}>
          Chat
        </TabButton>
        <TabButton active={tab === "memory"} onClick={() => setTab("memory")}>
          Memory
        </TabButton>
        <TabButton active={tab === "accuracy"} onClick={() => setTab("accuracy")}>
          Accuracy
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
  onApproval: (approved: boolean) => void;
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
