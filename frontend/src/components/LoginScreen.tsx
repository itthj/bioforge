import { useState } from "react";
import { login, register, type AuthUser } from "../api/auth";

/** Sign-in / register screen, shown by AuthGate when auth is enabled and no one is logged in. */
export function LoginScreen({
  allowRegistration,
  onAuthed,
}: {
  allowRegistration: boolean;
  onAuthed: (user: AuthUser) => void;
}) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "register") {
        await register(email, password, displayName || undefined);
      }
      const user = await login(email, password); // register auto-logs-in
      onAuthed(user);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex min-h-[70vh] max-w-sm flex-col justify-center px-4">
      <div className="mb-6 text-center">
        <div className="mb-2 inline-block h-3 w-3 rounded-full bg-accent" aria-hidden />
        <h1 className="text-2xl font-semibold tracking-tight text-fg">BioForge</h1>
        <p className="text-xs text-fg-subtle">
          {mode === "login" ? "Sign in to your projects + data." : "Create an account."}
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-3 rounded-lg border border-border bg-surface p-5 shadow-sm">
        {mode === "register" && (
          <label className="block">
            <span className="mb-1 block text-xs text-fg-subtle">Name (optional)</span>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="w-full rounded-md border border-border bg-surface-2 px-3 py-2 text-sm text-fg outline-none focus:border-accent"
              autoComplete="name"
            />
          </label>
        )}
        <label className="block">
          <span className="mb-1 block text-xs text-fg-subtle">Email</span>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-md border border-border bg-surface-2 px-3 py-2 text-sm text-fg outline-none focus:border-accent"
            autoComplete="username"
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs text-fg-subtle">Password</span>
          <input
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-md border border-border bg-surface-2 px-3 py-2 text-sm text-fg outline-none focus:border-accent"
            autoComplete={mode === "register" ? "new-password" : "current-password"}
          />
        </label>

        {error && <p className="text-xs text-danger">{error}</p>}

        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-md bg-accent px-3 py-2 text-sm font-medium text-bg shadow-sm transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {busy ? "…" : mode === "login" ? "Sign in" : "Create account"}
        </button>
      </form>

      {allowRegistration && (
        <button
          type="button"
          onClick={() => {
            setMode(mode === "login" ? "register" : "login");
            setError(null);
          }}
          className="mt-3 text-center text-xs text-fg-subtle hover:text-fg"
        >
          {mode === "login" ? "No account? Create one" : "Have an account? Sign in"}
        </button>
      )}
    </div>
  );
}
