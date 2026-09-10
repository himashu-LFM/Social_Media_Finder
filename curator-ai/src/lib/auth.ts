/** Client for the auth endpoints, plus token storage and an authed fetch wrapper. */
import { getPythonApiUrl } from "@/lib/processing-job";

const TOKEN_KEY = "curator-ai-auth-token-v1";

export type AuthUser = {
  id: number;
  email: string;
  name: string;
  role: string;
  /** Set on an admin-issued temporary password. The app is unusable until the
   *  holder replaces it — otherwise a temp password is just a password, known
   *  to whoever typed it. */
  must_change_password?: boolean;
};

export type AdminUser = AuthUser & {
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
};
export type AuthStatus = {
  auth_available: boolean;
  auth_required: boolean;
  has_accounts: boolean;
  /** Set when the server has a GOOGLE_CLIENT_ID configured. */
  google_enabled?: boolean;
  google_client_id?: string;
};

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

/**
 * Exchange a Google ID token for an application session.
 *
 * The token is only ever a claim until the server verifies it against Google's
 * public keys — this function does not, and must not, trust the email inside it.
 */
export async function googleSignIn(idToken: string): Promise<AuthUser> {
  const api = getPythonApiUrl();
  if (!api) throw new Error("NEXT_PUBLIC_PYTHON_API_URL is not set.");
  const res = await fetch(`${api}/api/auth/google`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id_token: idToken }),
  });
  if (!res.ok) {
    let detail = "Google sign-in failed.";
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
    // `?? null` matters: callers distinguish null ("no session") from a user,
    // and a response without a `user` key would otherwise hand back undefined
    // — which reads as neither, leaving the rail stuck on its loading dots.
    return ((await res.json()) as { user?: AuthUser | null }).user ?? null;
  } catch {
    return null;
  }
}

// ── password reset ──────────────────────────────────────────────────────────
//
// These two are public by necessity: someone who cannot sign in has to be able
// to use them. Both go through the API directly rather than authedFetch, which
// would attach a token they do not have.

async function publicPost<T>(path: string, body: unknown): Promise<T> {
  const api = getPythonApiUrl();
  if (!api) throw new Error("NEXT_PUBLIC_PYTHON_API_URL is not set.");
  const res = await fetch(`${api}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      detail = ((await res.json()) as { detail?: string }).detail ?? detail;
    } catch {
      /* non-JSON */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

/** Ask for a 6-digit code by email. Throws with the server's message. */
export function requestPasswordCode(email: string) {
  return publicPost<{ ok: boolean; emailed: boolean; expires_in_minutes: number }>(
    "/api/auth/forgot-password",
    { email: email.trim().toLowerCase() },
  );
}

/** Redeem the code and set a new password. */
export function resetPassword(email: string, code: string, newPassword: string) {
  return publicPost<{ ok: boolean; detail: string }>("/api/auth/reset-password", {
    email: email.trim().toLowerCase(),
    code: code.trim(),
    new_password: newPassword,
  });
}

/**
 * Change your own password — also how a temporary one is retired.
 *
 * The server ends every session on success, including this one, so the caller
 * must send the user back to sign in rather than carrying on.
 */
export async function changePassword(currentPassword: string, newPassword: string) {
  const res = await authedFetch("/api/auth/change-password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
  if (!res.ok) {
    let detail = `Could not change the password (${res.status}).`;
    try {
      detail = ((await res.json()) as { detail?: string }).detail ?? detail;
    } catch {
      /* non-JSON */
    }
    throw new Error(detail);
  }
  setToken(null);   // the server revoked every session, this one included
  return (await res.json()) as { ok: boolean; detail: string };
}

// ── admin ───────────────────────────────────────────────────────────────────
// The server enforces the admin role; hiding the UI is only courtesy.

export async function adminListUsers(): Promise<AdminUser[]> {
  const res = await authedFetch("/api/admin/users");
  if (!res.ok) throw new Error(await detailOf(res, "Could not load users."));
  return ((await res.json()) as { users: AdminUser[] }).users ?? [];
}

export async function adminCreateUser(input: {
  email: string;
  name?: string;
  role?: string;
  temp_password?: string;
}): Promise<{ user: AdminUser; temp_password: string; emailed: boolean }> {
  const res = await authedFetch("/api/admin/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error(await detailOf(res, "Could not create the account."));
  return (await res.json()) as { user: AdminUser; temp_password: string; emailed: boolean };
}

export async function adminResetPassword(userId: number): Promise<string> {
  const res = await authedFetch(`/api/admin/users/${userId}/reset-password`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(await detailOf(res, "Could not reset the password."));
  return ((await res.json()) as { temp_password: string }).temp_password;
}

export async function adminSetActive(userId: number, isActive: boolean): Promise<void> {
  const res = await authedFetch(`/api/admin/users/${userId}/active`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ is_active: isActive }),
  });
  if (!res.ok) throw new Error(await detailOf(res, "Could not update the account."));
}

async function detailOf(res: Response, fallback: string): Promise<string> {
  try {
    return ((await res.json()) as { detail?: string }).detail ?? fallback;
  } catch {
    return fallback;
  }
}
