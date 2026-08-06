"use client";

import { usePathname } from "next/navigation";
import { AuthGuard } from "@/components/AuthGuard";

/**
 * Decides whether a route sits behind the sign-in guard. /login must render
 * outside it — otherwise the guard would bounce an unauthenticated user to
 * /login, which would itself be guarded, an infinite redirect.
 */
const PUBLIC_ROUTES = ["/login"];

export function AppChrome({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  if (PUBLIC_ROUTES.some((r) => pathname?.startsWith(r))) {
    return <>{children}</>;
  }
  return <AuthGuard>{children}</AuthGuard>;
}
