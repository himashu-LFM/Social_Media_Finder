"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import {
  ArrowRight,
  CheckCheck,
  Eye,
  EyeOff,
  KeyRound,
  LoaderCircle,
  LockKeyhole,
} from "lucide-react";
import { AuthLayout, AuthMessage } from "@/components/AuthLayout";
import { changePassword } from "@/lib/auth";
import "../../auth-design.css";

/**
 * Change your own password, on the same shell as sign-in.
 *
 * Reached two ways: voluntarily from the user menu, and — with `?forced=1` —
 * straight after signing in with an admin-issued temporary password. The
 * forced variant is what stops a temporary password becoming a permanent one
 * that the admin who typed it also knows.
 *
 * The server ends every session on success, so this always finishes at /login.
 */
function ChangePasswordForm() {
  const router = useRouter();
  const forced = useSearchParams().get("forced") === "1";

  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mismatch = confirm.length > 0 && next !== confirm;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (next !== confirm) {
      setError("The two new passwords do not match.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await changePassword(current, next);
      router.replace("/login?changed=1");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change the password.");
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
    <AuthLayout
      heading={forced ? "Choose your password" : "Change password"}
      subtitle={
        forced
          ? "You signed in with a temporary password. Pick your own to continue."
          : "You will be signed out and asked to sign in again."
      }
    >
      {error && <AuthMessage tone="error">{error}</AuthMessage>}

      <form onSubmit={onSubmit}>
        <label htmlFor="ss-current">
          {forced ? "TEMPORARY PASSWORD" : "CURRENT PASSWORD"}
        </label>
        <div className="input-wrap">
          <KeyRound size={22} />
          <input
            id="ss-current"
            type="password"
            required
            autoFocus
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </div>

        <div className="password-label">
          <label htmlFor="ss-next">NEW PASSWORD</label>
        </div>
        <div className="input-wrap">
          <LockKeyhole size={22} />
          <input
            id="ss-next"
            type={showPassword ? "text" : "password"}
            required
            minLength={10}
            autoComplete="new-password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            placeholder="At least 10 characters"
          />
          {eye}
        </div>

        <div className="password-label">
          <label htmlFor="ss-confirm">CONFIRM NEW PASSWORD</label>
        </div>
        <div className="input-wrap" style={mismatch ? { borderColor: "rgba(255,86,120,.55)" } : undefined}>
          <CheckCheck size={22} />
          <input
            id="ss-confirm"
            type={showPassword ? "text" : "password"}
            required
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            aria-invalid={mismatch || undefined}
          />
        </div>
        {mismatch && (
          <p style={{ margin: "10px 0 0", color: "#ff9db0", fontSize: 12 }}>
            These do not match.
          </p>
        )}

        <button type="submit" className="signin" disabled={busy || mismatch}>
          <span>{busy ? "Updating…" : "Set password"}</span>
          {busy ? <LoaderCircle size={23} className="spin" /> : <ArrowRight size={23} />}
        </button>
      </form>

      {/* No escape hatch when forced — the account is unusable until the
          temporary password is replaced, so a "skip" link would only lead
          straight back here. */}
      {!forced && (
        <button type="button" className="link-btn block" onClick={() => router.back()}>
          Cancel
        </button>
      )}
    </AuthLayout>
  );
}

export default function ChangePasswordPage() {
  return (
    <Suspense fallback={null}>
      <ChangePasswordForm />
    </Suspense>
  );
}
