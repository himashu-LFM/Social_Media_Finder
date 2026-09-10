"use client";

import Link from "next/link";
import { useSyncExternalStore } from "react";
import { readPythonJobId } from "@/lib/processing-job";

/** sessionStorage is written elsewhere in the tab and never changes while this
 *  link is mounted, so there is nothing to subscribe to — but going through
 *  useSyncExternalStore is what makes reading it safe across the static
 *  prerender: the server snapshot is null, and the client's real value is
 *  picked up during hydration rather than in an effect that re-renders. */
const subscribe = () => () => {};
const serverSnapshot = () => null;

/**
 * "View Results" link that scopes the Results page to the CURRENT job.
 *
 * Reads the active Python job id from sessionStorage and links to
 * `/results?job=<id>`, so opening Results right after a new upload shows THIS
 * job's output — never a previous run's file that happens to be newest on disk.
 * Falls back to `/results` (newest) when there's no active job.
 */
export function ViewResultsLink({
  className,
  children,
  base = "/results",
}: {
  className?: string;
  children: React.ReactNode;
  base?: string;
}) {
  const jobId = useSyncExternalStore(subscribe, readPythonJobId, serverSnapshot);
  const href = jobId ? `${base}?job=${encodeURIComponent(jobId)}` : base;

  return (
    <Link href={href} className={className}>
      {children}
    </Link>
  );
}
