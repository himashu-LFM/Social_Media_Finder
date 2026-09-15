"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import Image from "next/image";
import { fetchAuthStatus, fetchMe, getToken, type AuthUser } from "@/lib/auth";
import "@/app/auth-design.css";

/**
 * Gates the app behind sign-in when the backend enforces auth. If auth is off
 * (no DATABASE_URL, or AUTH_REQUIRED=0), it renders children unchanged — local
 * development needs no login. The check is cheap: one /api/auth/status call,
 * then /api/auth/me only when a token is present.
 *
 * /login is exempt — gating the sign-in page behind sign-in would loop forever.
 */
const PUBLIC_ROUTES = ["/login"];
export function AuthGuard({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [allowed, setAllowed] = useState(false);
  const router = useRouter();
  const pathname = usePathname();
  const isPublic = PUBLIC_ROUTES.some((r) => (pathname ?? "").startsWith(r));

  useEffect(() => {
    if (isPublic) return;   // /login gates nothing; see the render below
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
  }, [router, pathname, isPublic]);

  // Public routes render immediately — no session check, no splash.
  if (!isPublic && (!ready || !allowed)) {
    return <SessionSplash />;
  }
  return <>{children}</>;
}

/**
 * Shown while the session is being confirmed, before every protected page.
 *
 * This is on screen for a few hundred milliseconds, so it has to read as
 * "loading" instantly. The mark at the centre does that work — a bare spinner
 * on a dark page looks like something has gone wrong, whereas the logo makes
 * the same wait look intentional. Styles live in auth-design.css so it shares
 * the sign-in screen's palette rather than being a third look.
 */
function SessionSplash() {
  return (
    <div className="ss-splash" role="status" aria-live="polite">
      <div className="ss-splash-inner">
        <div className="ss-splash-ring">
          <div className="ss-splash-mark">
            <Image src="/listenfirst-mark.jpg" alt="" width={48} height={48} priority />
          </div>
        </div>
        <p className="ss-splash-text">Checking your session</p>
        <div className="ss-splash-dots" aria-hidden>
          <i />
          <i />
          <i />
        </div>
      </div>
      <span className="sr-only">Loading</span>
    </div>
  );
}
