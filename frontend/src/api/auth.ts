import { clearToken, setToken } from "../lib/auth";

export interface AuthUser {
  id: string;
  email: string;
  display_name: string | null;
}

async function _detail(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
    if (Array.isArray(body?.detail)) return body.detail.map((d: { msg?: string }) => d.msg ?? "").join("; ");
  } catch {
    /* fall through */
  }
  return fallback;
}

/** Current user, or null when auth is on and we're not logged in (401). */
export async function me(): Promise<AuthUser | null> {
  const res = await fetch("/auth/me");
  if (res.status === 401) return null;
  if (!res.ok) throw new Error(`/auth/me failed (HTTP ${res.status})`);
  return res.json() as Promise<AuthUser>;
}

export async function login(email: string, password: string): Promise<AuthUser> {
  const res = await fetch("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw new Error(await _detail(res, "Incorrect email or password."));
  const data = (await res.json()) as { token: string; user: AuthUser };
  setToken(data.token); // the interceptor will now attach it to every request
  return data.user;
}

export async function register(email: string, password: string, displayName?: string): Promise<AuthUser> {
  const res = await fetch("/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, display_name: displayName || null }),
  });
  if (!res.ok) throw new Error(await _detail(res, "Registration failed."));
  return res.json() as Promise<AuthUser>;
}

export async function logout(): Promise<void> {
  try {
    await fetch("/auth/logout", { method: "POST" });
  } catch {
    /* best-effort; we clear the local token regardless */
  }
  clearToken();
}
