import { useState } from "react";
import { ApprovalCard } from "./components/ApprovalCard";
import { ChatInput } from "./components/ChatInput";
import { FinalCard } from "./components/FinalCard";
import { MemoryInspector } from "./components/MemoryInspector";
import { ProjectSwitcher } from "./components/ProjectSwitcher";
import { TraceView } from "./components/TraceView";
import { streamAgentApprove, streamAgentRun } from "./api/agent";
import type { AgentDoneEvent, AgentStep, SseEvent } from "./types/agent";

type RunState = "idle" | "running" | "done" | "pending_approval" | "error";
type Tab = "chat" | "memory";

const DEFAULT_PROJECT_ID = "default-project";

export function App() {
  const [projectId, setProjectId] = useState(DEFAULT_PROJECT_ID);
  const [tab, setTab] = useState<Tab>("chat");

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
      await consume(streamAgentRun({ goal, projectId }));
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
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">BioForge</h1>
          <p className="text-xs text-slate-500">
            Agentic AI bioinformatics — type a goal, watch the agent reason.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {runState !== "idle" && tab === "chat" && (
            <button
              type="button"
              onClick={reset}
              className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 shadow-sm hover:bg-slate-50"
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

      <nav className="mb-6 flex gap-1 border-b border-slate-200">
        <TabButton active={tab === "chat"} onClick={() => setTab("chat")}>
          Chat
        </TabButton>
        <TabButton active={tab === "memory"} onClick={() => setTab("memory")}>
          Memory
        </TabButton>
      </nav>

      {tab === "chat" ? (
        <ChatPanel
          steps={steps}
          done={done}
          error={error}
          inputDisabled={inputDisabled}
          runState={runState}
          onSubmit={handleSubmit}
          onApproval={handleApproval}
        />
      ) : (
        <MemoryInspector projectId={projectId} />
      )}
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
      className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${
        active
          ? "border-slate-900 text-slate-900"
          : "border-transparent text-slate-500 hover:text-slate-700"
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
  onSubmit,
  onApproval,
}: {
  steps: AgentStep[];
  done: AgentDoneEvent | null;
  error: string | null;
  inputDisabled: boolean;
  runState: RunState;
  onSubmit: (goal: string) => void;
  onApproval: (approved: boolean) => void;
}) {
  return (
    <>
      <ChatInput onSubmit={onSubmit} disabled={inputDisabled} />

      {error && (
        <div className="mt-4 rounded-md border border-rose-300 bg-rose-50 p-3 text-sm text-rose-800">
          {error}
        </div>
      )}

      {(steps.length > 0 || runState === "running") && (
        <section className="mt-6 space-y-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Trace
          </h2>
          <TraceView steps={steps} />
        </section>
      )}

      {done && (
        <section className="mt-6 space-y-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            {done.status === "pending_approval" ? "Approval" : "Result"}
          </h2>
          {done.status === "pending_approval" ? (
            <ApprovalCard
              done={done}
              onDecision={onApproval}
              disabled={runState === "running"}
            />
          ) : (
            <FinalCard done={done} />
          )}
        </section>
      )}
    </>
  );
}
