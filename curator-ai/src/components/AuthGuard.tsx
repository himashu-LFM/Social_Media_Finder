"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { fetchAuthStatus, fetchMe, getToken, type AuthUser } from "@/lib/auth";

/**
 * Gates the app behind sign-in when the backend enforces auth. If auth is off
 * (no DATABASE_URL, or AUTH_REQUIRED=0), it renders children unchanged — local
 * development needs no login. The check is cheap: one /api/auth/status call,
 * then /api/auth/me only when a token is present.
 */
export function AuthGuard({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [allowed, setAllowed] = useState(false);
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    let alive = true;
    void (async () => {
      const status = await fetchAuthStatus();
      if (!alive) return;

      // Auth unavailable or disabled → open app.
      if (!status || !status.auth_required) {
        setAllowed(true);
        setReady(true);
        return;
      }
      // Enforced: require a token that still resolves to a user.
      const user: AuthUser | null = getToken() ? await fetchMe() : null;
      if (!alive) return;
      if (user) {
        setAllowed(true);
        setReady(true);
      } else {
        router.replace(`/login?next=${encodeURIComponent(pathname ?? "/")}`);
      }
    })();
    return () => {
      alive = false;
    };
  }, [router, pathname]);

  if (!ready || !allowed) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <div className="relative h-12 w-12">
            <div className="absolute inset-0 animate-spin rounded-full border-2 border-primary/20 border-t-primary" />
            <span className="material-symbols-outlined absolute inset-0 flex items-center justify-center text-primary">
              lock
            </span>
          </div>
          <p className="text-sm font-semibold text-slate-500">Checking your session…</p>
        </div>
      </div>
    );
  }
  return <>{children}</>;
}
