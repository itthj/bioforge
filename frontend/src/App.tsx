import { useState } from "react";
import { ChatInput } from "./components/ChatInput";
import { TraceView } from "./components/TraceView";
import { ApprovalCard } from "./components/ApprovalCard";
import { FinalCard } from "./components/FinalCard";
import { streamAgentApprove, streamAgentRun } from "./api/agent";
import type { AgentDoneEvent, AgentStep, SseEvent } from "./types/agent";

type RunState = "idle" | "running" | "done" | "pending_approval" | "error";

export function App() {
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
      await consume(streamAgentRun({ goal }));
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

  const inputDisabled = runState === "running" || runState === "pending_approval";

  return (
    <div className="mx-auto max-w-3xl px-4 py-8">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">BioForge</h1>
          <p className="text-xs text-slate-500">
            Agentic AI bioinformatics — type a goal, watch the agent reason.
          </p>
        </div>
        {runState !== "idle" && (
          <button
            type="button"
            onClick={reset}
            className="rounded-md border border-slate-300 bg-white px-3 py-1 text-xs font-medium text-slate-700 shadow-sm hover:bg-slate-50"
          >
            New goal
          </button>
        )}
      </header>

      <ChatInput onSubmit={handleSubmit} disabled={inputDisabled} />

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
              onDecision={handleApproval}
              disabled={runState === "running"}
            />
          ) : (
            <FinalCard done={done} />
          )}
        </section>
      )}
    </div>
  );
}
