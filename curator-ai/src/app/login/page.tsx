"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { fetchAuthStatus, getToken, login } from "@/lib/auth";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next") || "/discovery";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Already signed in, or auth is off → skip the form entirely.
  useEffect(() => {
    let alive = true;
    void (async () => {
      const status = await fetchAuthStatus();
      if (!alive) return;
      if (!status || !status.auth_required) {
        router.replace(next);
      } else if (getToken()) {
        router.replace(next);
      } else if (!status.has_accounts) {
        setNotice("No accounts exist yet. Ask an administrator to run create_user.py.");
      }
    })();
    return () => {
      alive = false;
    };
  }, [router, next]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email.trim(), password);
      router.replace(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed.");
      setBusy(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden px-4">
      {/* Ambient brand glow — the one place a flourish belongs. */}
      <div aria-hidden className="lf-login-orb lf-login-orb-a" />
      <div aria-hidden className="lf-login-orb lf-login-orb-b" />

      <div className="lf-login-card relative z-10 w-full max-w-[400px]">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-5 flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/15 ring-1 ring-primary/30">
            <span className="material-symbols-outlined text-3xl text-primary">verified_user</span>
          </div>
          <h1 className="text-2xl font-black tracking-tight text-slate-50">Curator AI</h1>
          <p className="mt-1.5 text-sm text-slate-400">Sign in to the verification workspace</p>
        </div>

        {notice && (
          <div className="mb-5 rounded-xl border border-sky-500/30 bg-sky-500/10 px-4 py-3 text-xs text-sky-200">
            {notice}
          </div>
        )}

        <form onSubmit={onSubmit} className="space-y-4">
          <label className="block">
            <span className="mb-1.5 block text-xs font-bold uppercase tracking-wider text-slate-400">
              Email
            </span>
            <div className="lf-field">
              <span className="material-symbols-outlined text-lg text-slate-500">mail</span>
              <input
                type="email"
                required
                autoFocus
                autoComplete="username"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@listenfirstmedia.com"
                className="w-full bg-transparent text-sm text-slate-100 outline-none placeholder:text-slate-600"
              />
            </div>
          </label>

          <label className="block">
            <span className="mb-1.5 block text-xs font-bold uppercase tracking-wider text-slate-400">
              Password
            </span>
            <div className="lf-field">
              <span className="material-symbols-outlined text-lg text-slate-500">lock</span>
              <input
                type={showPw ? "text" : "password"}
                required
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••••"
                className="w-full bg-transparent text-sm text-slate-100 outline-none placeholder:text-slate-600"
              />
              <button
                type="button"
                onClick={() => setShowPw((v) => !v)}
                aria-label={showPw ? "Hide password" : "Show password"}
                className="cursor-pointer text-slate-500 transition hover:text-slate-300"
              >
                <span className="material-symbols-outlined text-lg">
                  {showPw ? "visibility_off" : "visibility"}
                </span>
              </button>
            </div>
          </label>

          {error && (
            <div
              role="alert"
              className="flex items-center gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-xs font-medium text-rose-200"
            >
              <span className="material-symbols-outlined text-base">error</span>
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={busy}
            className="lf-login-btn group flex w-full cursor-pointer items-center justify-center gap-2 rounded-xl bg-primary px-4 py-3 text-sm font-bold text-slate-950 transition disabled:cursor-wait disabled:opacity-70"
          >
            {busy ? (
              <>
                <span className="material-symbols-outlined animate-spin text-lg">progress_activity</span>
                Signing in…
              </>
            ) : (
              <>
                Sign in
                <span className="material-symbols-outlined text-lg transition-transform group-hover:translate-x-0.5">
                  arrow_forward
                </span>
              </>
            )}
          </button>
        </form>

        <p className="mt-6 text-center text-[11px] text-slate-600">
          ListenFirst · authorized access only
        </p>
      </div>
    </div>
  );
}

export default function LoginPage() {
  // useSearchParams needs a Suspense boundary in this Next version.
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
