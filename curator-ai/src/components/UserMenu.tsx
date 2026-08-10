"use client";

import { useEffect, useState } from "react";
import { fetchAuthStatus, fetchMe, logout, type AuthUser } from "@/lib/auth";

/** Signed-in identity + sign-out, shown at the foot of the sidebar. Renders
 *  nothing when auth is disabled, so it never clutters local dev. */
export function UserMenu() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [enforced, setEnforced] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    void (async () => {
      const status = await fetchAuthStatus();
      if (!alive || !status?.auth_required) return;
      setEnforced(true);
      setUser(await fetchMe());
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (!enforced || !user) return null;

  const initials = (user.name || user.email)
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((s) => s[0]?.toUpperCase())
    .join("");

  return (
    <div className="mt-3 flex items-center gap-3 rounded-xl border border-white/8 bg-slate-950/40 p-3">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary/15 text-xs font-black text-primary ring-1 ring-primary/25">
        {initials || "?"}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-xs font-bold text-slate-200">{user.name || user.email}</p>
        <p className="truncate text-[10px] uppercase tracking-wider text-slate-500">{user.role}</p>
      </div>
      <button
        type="button"
        disabled={busy}
        aria-label="Sign out"
        title="Sign out"
        onClick={async () => {
          setBusy(true);
          await logout();
          window.location.href = "/login";
        }}
        className="cursor-pointer rounded-lg p-1.5 text-slate-500 transition hover:bg-white/5 hover:text-rose-300 disabled:opacity-50"
      >
        <span className={`material-symbols-outlined text-lg ${busy ? "animate-spin" : ""}`}>
          {busy ? "progress_activity" : "logout"}
        </span>
      </button>
    </div>
  );
}
