"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { readPythonJobId } from "@/lib/processing-job";

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
  const [href, setHref] = useState(base);

  useEffect(() => {
    const jid = readPythonJobId();
    setHref(jid ? `${base}?job=${encodeURIComponent(jid)}` : base);
  }, [base]);

  return (
    <Link href={href} className={className}>
      {children}
    </Link>
  );
}
