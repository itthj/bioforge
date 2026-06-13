// Bearer-token store + a one-time global fetch interceptor.
//
// All API calls in this app use relative URLs (/agent, /projects, /traces, ...). Rather than thread
// a token through every call site (including the raw-fetch SSE consumer), we install ONE fetch
// wrapper at startup that attaches `Authorization: Bearer <token>` to same-origin requests when a
// token is set. The token is persisted in localStorage so a reload keeps you signed in.

const TOKEN_KEY = "bioforge_token";
let _token: string | null | undefined; // undefined = not yet read from storage

export function getToken(): string | null {
  if (_token === undefined) {
    try {
      _token = localStorage.getItem(TOKEN_KEY);
    } catch {
      _token = null;
    }
  }
  return _token ?? null;
}

export function setToken(token: string): void {
  _token = token;
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* storage may be unavailable (private mode) -- in-memory token still works for the session */
  }
}

export function clearToken(): void {
  _token = null;
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

function _urlOf(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

function _isSameOrigin(url: string): boolean {
  // Relative URLs (our API) are same-origin by definition.
  if (url.startsWith("/") && !url.startsWith("//")) return true;
  try {
    return new URL(url, window.location.origin).origin === window.location.origin;
  } catch {
    return false;
  }
}

let _installed = false;

/** Install the global fetch interceptor exactly once. Call at app startup, before rendering. */
export function installAuthFetch(): void {
  if (_installed) return;
  _installed = true;
  const original = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> => {
    const token = getToken();
    if (!token || !_isSameOrigin(_urlOf(input))) return original(input, init);
    const headers = new Headers(init.headers ?? (input instanceof Request ? input.headers : undefined));
    if (!headers.has("Authorization")) headers.set("Authorization", `Bearer ${token}`);
    return original(input, { ...init, headers });
  };
}
