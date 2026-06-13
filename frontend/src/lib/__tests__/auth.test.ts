import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { clearToken, getToken, installAuthFetch, setToken } from "../auth";

describe("token store", () => {
  afterEach(() => clearToken());

  it("round-trips through localStorage", () => {
    expect(getToken()).toBeNull();
    setToken("abc123");
    expect(getToken()).toBe("abc123");
    clearToken();
    expect(getToken()).toBeNull();
  });
});

describe("auth fetch interceptor", () => {
  let original: ReturnType<typeof vi.fn>;
  const realFetch = window.fetch;

  beforeAll(() => {
    original = vi.fn(async () => new Response("{}", { status: 200 }));
    window.fetch = original as unknown as typeof fetch;
    installAuthFetch(); // captures `original` as the underlying fetch
  });
  afterAll(() => {
    window.fetch = realFetch;
  });
  afterEach(() => {
    clearToken();
    original.mockClear();
  });

  function authHeaderOf(callIndex = 0): string | null {
    const init = original.mock.calls[callIndex]?.[1] as RequestInit | undefined;
    return new Headers(init?.headers).get("Authorization");
  }

  it("attaches the bearer token to same-origin (relative) requests", async () => {
    setToken("tok-xyz");
    await window.fetch("/projects/default-project/files");
    expect(authHeaderOf()).toBe("Bearer tok-xyz");
  });

  it("does NOT attach the token to cross-origin requests", async () => {
    setToken("tok-xyz");
    await window.fetch("https://ncbi.example.com/blast");
    expect(authHeaderOf()).toBeNull();
  });

  it("attaches nothing when there is no token", async () => {
    await window.fetch("/auth/me");
    expect(authHeaderOf()).toBeNull();
  });

  it("does not clobber an explicit Authorization header", async () => {
    setToken("tok-xyz");
    await window.fetch("/x", { headers: { Authorization: "Bearer explicit" } });
    expect(authHeaderOf()).toBe("Bearer explicit");
  });
});
