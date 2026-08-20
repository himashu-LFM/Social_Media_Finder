"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AppMobileNav } from "@/components/AppMobileNav";
import { AppPageHeader } from "@/components/AppPageHeader";
import { ResultsAnalysisButton } from "@/components/ResultsAnalysisButton";
import { AppSidebar } from "@/components/AppSidebar";
import { ResultsExportButton } from "@/components/ResultsExportButton";
import { ResultsTable } from "@/components/ResultsTable";
import { authedFetch } from "@/lib/auth";
import { getPythonApiUrl } from "@/lib/processing-job";
import { mapRecordToRow } from "@/lib/results-mapper";
import type { ResultRow } from "@/types/results";

/**
 * Results, loaded in the browser.
 *
 * This used to be a server component that called the API and, failing that,
 * read export workbooks off the local filesystem. Both halves were broken once
 * deployed: the bearer token lives in localStorage, so a server-side call
 * carries no credentials and gets a 401, and the frontend container has no
 * access to the API's exports directory. The page therefore rendered empty in
 * production while working perfectly on a laptop.
 *
 * Fetching here fixes that and is also what makes a static export possible —
 * no server, no Node runtime, just a CDN and the API.
 */
export function ResultsClient() {
  const searchParams = useSearchParams();
  const jobId = searchParams.get("job") ?? "";

  const [rows, setRows] = useState<ResultRow[]>([]);
  const [latestFileName, setLatestFileName] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadWarning, setLoadWarning] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);

    void (async () => {
      if (!getPythonApiUrl()) {
        if (alive) {
          setLoadError(
            "NEXT_PUBLIC_PYTHON_API_URL is not set, so there is no API to read results from.",
          );
          setLoading(false);
        }
        return;
      }
      try {
        const url = jobId
          ? `/api/results/latest?job_id=${encodeURIComponent(jobId)}`
          : "/api/results/latest";
        const res = await authedFetch(url, { cache: "no-store" });
        if (!alive) return;

        if (!res.ok) {
          // 401 is handled inside authedFetch, which redirects to /login.
          setLoadError(`Could not load results (HTTP ${res.status}).`);
          setLoading(false);
          return;
        }

        const data = (await res.json()) as {
          rows?: Record<string, unknown>[];
          filename?: string | null;
          warning?: string | null;
          error?: string | null;
          pending?: boolean;
        };
        if (!alive) return;

        if (data.pending) {
          setRows([]);
          setLoadWarning(
            "This run is still processing — results will appear here once it finishes.",
          );
        } else {
          setRows((data.rows ?? []).map((r) => mapRecordToRow(r)));
          setLatestFileName(data.filename ?? null);
          setLoadWarning(data.warning ?? null);
          setLoadError(data.error ?? null);
        }
      } catch (err) {
        if (alive) {
          setLoadError(err instanceof Error ? err.message : "Could not reach the API.");
        }
      } finally {
        if (alive) setLoading(false);
      }
    })();

    return () => {
      alive = false;
    };
  }, [jobId]);

  const totalRows = rows.length;
  const highCount = rows.filter((r) => r.confidence > 0.8).length;
  const ambiguousCount = rows.filter(
    (r) => r.confidence >= 0.5 && r.confidence <= 0.8,
  ).length;
  const avgConfidence =
    totalRows === 0 ? 0 : rows.reduce((acc, r) => acc + r.confidence, 0) / totalRows;

  return (
    <div className="relative flex min-h-screen flex-col md:flex-row">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_100%_0%,rgba(242,209,0,0.06),transparent_30%)]"
      />
      <AppSidebar />

      <main className="relative z-10 flex-1 p-4 pb-32 md:ml-64 md:p-8">
        <AppPageHeader
          title="Results"
          subtitle="Verification output"
          icon="table_chart"
          actions={
            <>
              <ResultsAnalysisButton />
              <ResultsExportButton rows={rows} sourceFileName={latestFileName} />
            </>
          }
        />

        <div className="mx-auto max-w-7xl space-y-6">
          <div className="lf-enter lf-card p-4 sm:p-5">
            <div className="flex flex-wrap items-start gap-3">
              <span className="material-symbols-outlined text-primary">schema</span>
              <div className="min-w-0 flex-1">
                <p className="text-sm text-slate-300">
                  Output schema: Talent Name, Wikipedia URL, and per platform a link, verification
                  Status, Confidence, and Reason.
                </p>
                <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
                  <span className="text-slate-400">Status:</span>
                  <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 font-semibold text-emerald-300">
                    Verified
                  </span>
                  <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 font-semibold text-amber-300">
                    Manual Review Needed
                  </span>
                  <span className="rounded-full border border-rose-500/30 bg-rose-500/10 px-2 py-0.5 font-semibold text-rose-300">
                    Wrong
                  </span>
                  <span className="rounded-full border border-slate-500/30 bg-slate-500/10 px-2 py-0.5 font-semibold text-slate-400">
                    Not Found
                  </span>
                  <span
                    className="rounded-full border border-sky-500/30 bg-sky-500/10 px-2 py-0.5 font-semibold text-sky-300"
                    title="The run was stopped before this platform was searched — this is not a verified absence."
                  >
                    Not Checked
                  </span>
                </div>
                <p className="mt-2 text-xs text-slate-500">
                  {loading
                    ? "Loading the latest run…"
                    : `Latest export: ${latestFileName ?? "none yet"}`}
                </p>
                {loadWarning && <p className="mt-1 text-xs text-sky-400">{loadWarning}</p>}
                {loadError && (
                  <p className="mt-1 text-xs text-amber-400" role="alert">
                    {loadError}
                  </p>
                )}
              </div>
            </div>
          </div>

          <div className="lf-enter lf-enter-delay-1">
            {loading ? <ResultsSkeleton /> : <ResultsTable rows={rows} />}
          </div>

          <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
            <div className="lf-enter lf-enter-delay-2 lf-card lf-card-hover lf-stat-glow-emerald p-6">
              <div className="inline-flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-slate-500">
                <span className="material-symbols-outlined text-base text-emerald-400">verified</span>
                High Confidence Rows
              </div>
              <div className="mt-2 text-4xl font-black text-emerald-400">{highCount}</div>
            </div>
            <div className="lf-enter lf-enter-delay-2 lf-card lf-card-hover lf-stat-glow-amber p-6">
              <div className="inline-flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-slate-500">
                <span className="material-symbols-outlined text-base text-amber-400">help</span>
                Ambiguous Rows
              </div>
              <div className="mt-2 text-4xl font-black text-amber-400">{ambiguousCount}</div>
            </div>
            <div className="lf-enter lf-enter-delay-3 lf-card lf-card-hover lf-stat-glow-primary border-primary/25 bg-primary/10 p-6">
              <div className="inline-flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-primary/80">
                <span className="material-symbols-outlined text-base">analytics</span>
                Average Confidence
              </div>
              <div className="mt-2 text-4xl font-black text-slate-950">
                {(avgConfidence * 100).toFixed(1)}%
              </div>
            </div>
          </div>

          <div className="lf-enter lf-enter-delay-3 lf-gradient-border lf-card border-primary/25 bg-primary/10 p-6">
            <div className="relative z-10">
              <div className="inline-flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-primary">
                <span className="material-symbols-outlined text-base">grade</span>
                Final Confidence Score
              </div>
              <div className="mt-2 text-3xl font-black text-slate-950">
                {(avgConfidence * 100).toFixed(2)}%
              </div>
              <p className="mt-2 text-xs text-slate-700">
                Computed as average row confidence across all processed records.
              </p>
            </div>
          </div>
        </div>
      </main>

      <AppMobileNav />
    </div>
  );
}

function ResultsSkeleton() {
  return (
    <div className="lf-card space-y-3 p-6" aria-busy="true" aria-label="Loading results">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="h-9 animate-pulse rounded-lg bg-slate-800/60" />
      ))}
    </div>
  );
}
