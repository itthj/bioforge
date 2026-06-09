import { afterEach, describe, expect, it, vi } from "vitest";
import { cancelRun } from "../agent";

describe("cancelRun", () => {
  afterEach(() => vi.restoreAllMocks());

  it("POSTs to /agent/{id}/cancel", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 200 }));

    await cancelRun("trace_42");

    expect(fetchSpy).toHaveBeenCalledWith("/agent/trace_42/cancel", { method: "POST" });
  });

  it("swallows network errors (the local Stop/abort is what the user sees)", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network down"));
    await expect(cancelRun("trace_42")).resolves.toBeUndefined();
  });
});
