// SSE consumer for /agent/run/stream and /agent/{id}/approve/stream.
//
// Native EventSource only does GET; both our streaming endpoints are POST so we use
// fetch() with a ReadableStream reader. Each chunk is decoded, buffered, and split on
// the SSE block delimiter "\n\n". Each block becomes one yielded event.
//
// Caller pattern:
//   for await (const ev of streamAgentRun({ goal, projectId })) { ... }
// The async generator yields strongly-typed SseEvent values.

import type { Autonomy, SseEvent } from "../types/agent";

const SSE_BLOCK_DELIMITER = "\n\n";

interface RawSseBlock {
  event: string;
  data: string;
}

function parseSseBlock(block: string): RawSseBlock | null {
  // Comment / keepalive line, e.g. ": ping". Skip silently.
  if (block.startsWith(":")) return null;

  let eventName = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event: ")) {
      eventName = line.slice(7).trim();
    } else if (line.startsWith("data: ")) {
      dataLines.push(line.slice(6));
    }
  }
  if (dataLines.length === 0) return null;
  return { event: eventName, data: dataLines.join("\n") };
}

function asSseEvent(raw: RawSseBlock): SseEvent {
  try {
    const parsed = JSON.parse(raw.data);
    return { event: raw.event, data: parsed } as SseEvent;
  } catch {
    // Backend always JSON-encodes data payloads; if parsing fails, surface the raw
    // text as an error event rather than crashing the consumer.
    return {
      event: "error",
      data: { message: `Unparseable SSE payload: ${raw.data.slice(0, 200)}` },
    };
  }
}

async function* streamSse(
  response: Response,
): AsyncGenerator<SseEvent, void, unknown> {
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    yield {
      event: "error",
      data: { message: `HTTP ${response.status}: ${text || response.statusText}` },
    };
    return;
  }
  if (!response.body) {
    yield { event: "error", data: { message: "Response has no body" } };
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let delimiterIdx;
      while ((delimiterIdx = buffer.indexOf(SSE_BLOCK_DELIMITER)) >= 0) {
        const block = buffer.slice(0, delimiterIdx);
        buffer = buffer.slice(delimiterIdx + SSE_BLOCK_DELIMITER.length);

        const raw = parseSseBlock(block);
        if (raw !== null) yield asSseEvent(raw);
      }
    }
    // Flush any trailing partial block (rare; backend always terminates with \n\n)
    const tail = buffer.trim();
    if (tail) {
      const raw = parseSseBlock(tail);
      if (raw !== null) yield asSseEvent(raw);
    }
  } finally {
    reader.releaseLock();
  }
}

export interface AgentRunInput {
  goal: string;
  projectId?: string;
  autonomy?: Autonomy;
}

export async function* streamAgentRun(
  input: AgentRunInput,
  signal?: AbortSignal,
): AsyncGenerator<SseEvent, void, unknown> {
  // `signal` lets the caller abort the run: aborting closes the SSE connection, which the
  // backend detects as a client disconnect and cancels the in-flight agent task.
  const response = await fetch("/agent/run/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      goal: input.goal,
      project_id: input.projectId ?? "default-project",
      autonomy: input.autonomy ?? "auto",
    }),
    signal,
  });
  yield* streamSse(response);
}

export interface ApprovalInput {
  traceId: string;
  approved: boolean;
  reason?: string;
}

export async function* streamAgentApprove(
  input: ApprovalInput,
  signal?: AbortSignal,
): AsyncGenerator<SseEvent, void, unknown> {
  const response = await fetch(`/agent/${input.traceId}/approve/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      approved: input.approved,
      reason: input.reason,
    }),
    signal,
  });
  yield* streamSse(response);
}
