"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { MAIN_NAV, isNavActive } from "@/config/navigation";
import { fetchMe, logout, type AuthUser } from "@/lib/auth";

/**
 * The 232px navigation rail, from the Claude Design handoff.
 *
 * Differences from the old sidebar, all from the design: it no longer
 * collapses (the rail is already narrow, and a collapse toggle was one more
 * piece of state to keep in sync with the main column), the account card is
 * pinned to the bottom with its own change-password and sign-out buttons, and
 * the active row is a yellow tint plus a trailing dot rather than a bar.
 *
 * Admin-only links are hidden from analysts. That is presentation only — the
 * API returns 403 to a non-admin whatever the rail shows.
 */
export function AppSidebar() {
  const pathname = usePathname() ?? "";
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [authOff, setAuthOff] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  useEffect(() => {
    let alive = true;
    void fetchMe().then((me) => {
      if (!alive) return;
      // null means auth is switched off entirely (local dev with no database).
      setAuthOff(me === null);
      setUser(me);
    });
    return () => {
      alive = false;
    };
  }, []);

  const isAdmin = authOff || user?.role?.toLowerCase() === "admin";
  const items = MAIN_NAV.filter((item) => !item.adminOnly || isAdmin);

  const label = user?.name || user?.email || (authOff ? "Local session" : "…");
  const initials =
    (user?.name || user?.email || "?")
      .split(/[\s@._-]+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((p) => p[0]?.toUpperCase())
      .join("") || "?";

  return (
    <nav className="sc-rail" aria-label="Main">
      <Link href="/discovery" className="sc-rail-brand">
        <span className="sc-rail-dot" aria-hidden />
        <span className="sc-rail-name">ListenFirst</span>
        <span className="sc-rail-sub">Scout</span>
      </Link>

      {items.map((item) => {
        const active = isNavActive(pathname, item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            className="sc-nav-item"
            aria-current={active ? "page" : undefined}
          >
            <span className="material-symbols-outlined" style={{ fontSize: 19 }}>
              {item.icon}
            </span>
            {item.label}
            {active ? (
              <span className="sc-nav-active-dot" aria-hidden />
            ) : item.adminOnly ? (
              <span className="sc-nav-tag">Admin</span>
            ) : null}
          </Link>
        );
      })}

      <div className="sc-rail-user">
        <span className="sc-avatar" aria-hidden>
          {initials}
        </span>
        <span style={{ minWidth: 0, flex: 1 }}>
          <span className="sc-rail-user-name">{label}</span>
          <span className="sc-rail-user-role">
            {authOff ? "No auth" : user?.role || "—"}
          </span>
        </span>
        <Link
          href="/account/password"
          aria-label="Change password"
          title="Change password"
          className="sc-icon-btn"
        >
          <span className="material-symbols-outlined" style={{ fontSize: 17 }}>
            lock_reset
          </span>
        </Link>
        <button
          type="button"
          aria-label="Sign out"
          title="Sign out"
          disabled={signingOut}
          className="sc-icon-btn danger"
          onClick={async () => {
            setSigningOut(true);
            await logout();
            router.replace("/login");
          }}
        >
          <span
            className={`material-symbols-outlined${signingOut ? " animate-spin" : ""}`}
            style={{ fontSize: 17 }}
          >
            {signingOut ? "progress_activity" : "logout"}
          </span>
        </button>
      </div>
    </nav>
  );
}
