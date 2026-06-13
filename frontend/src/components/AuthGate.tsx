import { useEffect, useState, type ReactNode } from "react";
import { logout as apiLogout, me, type AuthUser } from "../api/auth";
import { getConfig } from "../api/config";
import { LoginScreen } from "./LoginScreen";

export interface AuthContext {
  /** The signed-in user, or null when auth is disabled (single-user mode). */
  user: AuthUser | null;
  authEnabled: boolean;
  logout: () => Promise<void>;
}

/**
 * Gates the app on authentication. Reads /config:
 *   - auth disabled  -> render the app immediately (single-user, no login UI).
 *   - auth enabled    -> require a valid session; otherwise show the LoginScreen.
 * Fails OPEN if /config is unreachable (e.g. backend down in dev), so the app still loads.
 */
export function AuthGate({ children }: { children: (ctx: AuthContext) => ReactNode }) {
  const [phase, setPhase] = useState<"loading" | "login" | "ready">("loading");
  const [user, setUser] = useState<AuthUser | null>(null);
  const [authEnabled, setAuthEnabled] = useState(false);
  const [allowRegistration, setAllowRegistration] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const cfg = await getConfig();
        if (cancelled) return;
        setAuthEnabled(cfg.auth_enabled);
        setAllowRegistration(cfg.auth_allow_registration);
        if (!cfg.auth_enabled) {
          setPhase("ready");
          return;
        }
        const current = await me();
        if (cancelled) return;
        if (current) {
          setUser(current);
          setPhase("ready");
        } else {
          setPhase("login");
        }
      } catch {
        // Backend unreachable -- don't trap the user on a spinner; let the app render.
        if (!cancelled) setPhase("ready");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function logout() {
    await apiLogout();
    setUser(null);
    setPhase("login");
  }

  if (phase === "loading") {
    return <div className="mx-auto max-w-3xl px-4 py-16 text-center text-sm text-fg-subtle">Loading…</div>;
  }
  if (phase === "login") {
    return (
      <LoginScreen
        allowRegistration={allowRegistration}
        onAuthed={(u) => {
          setUser(u);
          setPhase("ready");
        }}
      />
    );
  }
  return <>{children({ user, authEnabled, logout })}</>;
}
