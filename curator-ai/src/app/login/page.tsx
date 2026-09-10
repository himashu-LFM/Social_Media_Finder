"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { ArrowRight, Eye, EyeOff, LoaderCircle, LockKeyhole, KeyRound, Mail } from "lucide-react";
import { AuthLayout, AuthMessage } from "@/components/AuthLayout";
import { GoogleSignInButton } from "@/components/GoogleSignInButton";
import {
  fetchAuthStatus,
  getToken,
  login,
  requestPasswordCode,
  resetPassword,
} from "@/lib/auth";
import "../auth-design.css";

/**
 * Sign in, and reset a forgotten password.
 *
 * Three panels swapped in place inside the one card. There is deliberately
 * **no sign-up panel**: accounts are created by an admin under Users, because
 * a public registration form on an internal tool is an open door to anyone who
 * finds the URL.
 */
type Panel = "login" | "forgot" | "reset";

const COPY: Record<Panel, { heading: string; subtitle: string }> = {
  login: { heading: "Sign in", subtitle: "Sign in to the verification workspace" },
  forgot: {
    heading: "Reset password",
    subtitle: "We'll email you a code to reset your password",
  },
  reset: { heading: "Enter your code", subtitle: "Enter the code we emailed you" },
};

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next") || "/discovery";

  const [panel, setPanel] = useState<Panel>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(
    params.get("changed") === "1" ? "Password changed. Sign in with your new one." : null,
  );
  const [googleClientId, setGoogleClientId] = useState("");
  const firstField = useRef<HTMLInputElement>(null);

  // Focus the first field on desktop only. `autoFocus` on a phone scrolls the
  // browser straight past the hero to the form, so the page appears to open
  // half-way down on an empty stretch of the illustration.
  useEffect(() => {
    if (window.innerWidth >= 980) firstField.current?.focus();
  }, [panel]);

  // Already signed in, or auth is off → never show a login form.
  useEffect(() => {
    let alive = true;
    void (async () => {
      const status = await fetchAuthStatus();
      if (!alive) return;
      if (!status || !status.auth_required) {
        router.replace(next);
      } else if (getToken()) {
        router.replace(next);
      } else {
        if (status.google_enabled && status.google_client_id) {
          setGoogleClientId(status.google_client_id);
        }
        if (!status.has_accounts) {
          setNotice(
            "No accounts exist yet. An administrator creates the first one with " +
              "python scripts/create_user.py --admin.",
          );
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, [router, next]);

  function go(to: Panel) {
    setPanel(to);
    setError(null);
    setNotice(null);
  }

  async function onSignIn(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const user = await login(email.trim(), password);
      // A temporary password is not a password until it has been replaced.
      router.replace(user.must_change_password ? "/account/password?forced=1" : next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed.");
      setBusy(false);
    }
  }

  async function onRequestCode(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { emailed, expires_in_minutes } = await requestPasswordCode(email);
      setNotice(
        emailed
          ? `A ${expires_in_minutes}-minute code is on its way to ${email.trim()}.`
          : "Email is not configured on this server — ask an administrator for the " +
              "code, or for a new temporary password.",
      );
      setPanel("reset");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send a code.");
    } finally {
      setBusy(false);
    }
  }

  async function onReset(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await resetPassword(email, code, newPassword);
      setPassword("");
      setNewPassword("");
      setCode("");
      setPanel("login");
      setNotice("Password updated. Sign in with your new password.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reset the password.");
    } finally {
      setBusy(false);
    }
  }

  const eye = (
    <button
      type="button"
      className="eye"
      aria-label={showPassword ? "Hide password" : "Show password"}
      onClick={() => setShowPassword((v) => !v)}
    >
      {showPassword ? <EyeOff size={21} /> : <Eye size={21} />}
    </button>
  );

  return (
    <AuthLayout heading={COPY[panel].heading} subtitle={COPY[panel].subtitle}>
      {error && <AuthMessage tone="error">{error}</AuthMessage>}
      {notice && <AuthMessage tone="info">{notice}</AuthMessage>}

      {panel === "login" && (
        <>
          {googleClientId && (
            <div style={{ marginBottom: 26 }}>
              <GoogleSignInButton
                clientId={googleClientId}
                onSignedIn={() => router.replace(next)}
                onError={(message) => setError(message)}
              />
            </div>
          )}

          <form onSubmit={onSignIn}>
            <label htmlFor="ss-email">EMAIL</label>
            <div className="input-wrap">
              <Mail size={22} />
              <input
                id="ss-email"
                ref={firstField}
                type="email"
                required
                autoComplete="username"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@listenfirstmedia.com"
              />
            </div>

            <div className="password-label">
              <label htmlFor="ss-password">PASSWORD</label>
              <button type="button" className="forgot" onClick={() => go("forgot")}>
                Forgot?
              </button>
            </div>
            <div className="input-wrap">
              <LockKeyhole size={22} />
              <input
                id="ss-password"
                type={showPassword ? "text" : "password"}
                required
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••••"
              />
              {eye}
            </div>

            <Submit busy={busy} idle="Sign in" busyLabel="Signing in…" />
          </form>
        </>
      )}

      {panel === "forgot" && (
        <form onSubmit={onRequestCode}>
          <label htmlFor="ss-forgot-email">EMAIL</label>
          <div className="input-wrap">
            <Mail size={22} />
            <input
              id="ss-forgot-email"
              ref={firstField}
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@listenfirstmedia.com"
            />
          </div>

          <Submit busy={busy} idle="Email me a code" busyLabel="Sending…" />
          <button type="button" className="link-btn block" onClick={() => go("login")}>
            Back to sign in
          </button>
        </form>
      )}

      {panel === "reset" && (
        <form onSubmit={onReset}>
          <label htmlFor="ss-code">6-DIGIT CODE</label>
          <div className="input-wrap code">
            <KeyRound size={22} />
            <input
              id="ss-code"
              ref={firstField}
              type="text"
              required
              inputMode="numeric"
              pattern="[0-9]*"
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              placeholder="123456"
            />
          </div>

          <div className="password-label">
            <label htmlFor="ss-new-password">NEW PASSWORD</label>
          </div>
          <div className="input-wrap">
            <LockKeyhole size={22} />
            <input
              id="ss-new-password"
              type={showPassword ? "text" : "password"}
              required
              minLength={10}
              autoComplete="new-password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder="At least 10 characters"
            />
            {eye}
          </div>

          <Submit busy={busy} idle="Set new password" busyLabel="Updating…" />
          <div className="link-row">
            <button type="button" className="link-btn" onClick={() => go("forgot")}>
              Send another code
            </button>
            <button type="button" className="link-btn" onClick={() => go("login")}>
              Back to sign in
            </button>
          </div>
        </form>
      )}
    </AuthLayout>
  );
}

function Submit({
  busy,
  idle,
  busyLabel,
}: {
  busy: boolean;
  idle: string;
  busyLabel: string;
}) {
  return (
    <button type="submit" className="signin" disabled={busy}>
      <span>{busy ? busyLabel : idle}</span>
      {busy ? <LoaderCircle size={23} className="spin" /> : <ArrowRight size={23} />}
    </button>
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
