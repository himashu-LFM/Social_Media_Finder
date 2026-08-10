/** Client for the auth endpoints, plus token storage and an authed fetch wrapper. */
import { getPythonApiUrl } from "@/lib/processing-job";

const TOKEN_KEY = "curator-ai-auth-token-v1";

export type AuthUser = { id: number; email: string; name: string; role: string };
export type AuthStatus = { auth_available: boolean; auth_required: boolean; has_accounts: boolean };

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function setToken(token: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode */
  }
}

/**
 * fetch with the bearer token attached. On 401 it clears the dead token and
 * signals the caller to send the user back to sign-in, so an expired session
 * surfaces as a redirect rather than a wall of failed requests.
 */
export async function authedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const api = getPythonApiUrl();
  if (!api) throw new Error("NEXT_PUBLIC_PYTHON_API_URL is not set.");
  const token = getToken();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`${api}${path}`, { ...init, headers });
  if (res.status === 401) {
    setToken(null);
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
    }
  }
  return res;
}

export async function fetchAuthStatus(): Promise<AuthStatus | null> {
  const api = getPythonApiUrl();
  if (!api) return null;
  try {
    const res = await fetch(`${api}/api/auth/status`, { cache: "no-store" });
    return res.ok ? ((await res.json()) as AuthStatus) : null;
  } catch {
    return null;
  }
}

export async function login(email: string, password: string): Promise<AuthUser> {
  const api = getPythonApiUrl();
  if (!api) throw new Error("NEXT_PUBLIC_PYTHON_API_URL is not set.");
  const res = await fetch(`${api}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    let detail = "Sign-in failed.";
    try {
      detail = ((await res.json()) as { detail?: string }).detail ?? detail;
    } catch {
      /* non-JSON */
    }
    throw new Error(detail);
  }
  const data = (await res.json()) as { user: AuthUser; token: string };
  setToken(data.token);
  return data.user;
}

export async function logout(): Promise<void> {
  try {
    await authedFetch("/api/auth/logout", { method: "POST" });
  } finally {
    setToken(null);
  }
}

export async function fetchMe(): Promise<AuthUser | null> {
  try {
    const res = await authedFetch("/api/auth/me");
    if (!res.ok) return null;
    return ((await res.json()) as { user: AuthUser | null }).user;
  } catch {
    return null;
  }
}
