"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { AppMain } from "@/components/AppMain";
import { AppMobileNav } from "@/components/AppMobileNav";
import { AppPageHeader } from "@/components/AppPageHeader";
import { AppSidebar } from "@/components/AppSidebar";
import { authedFetch } from "@/lib/auth";
import { getPythonApiUrl } from "@/lib/processing-job";
import {
  mapRecordToRow,
  RESULT_PLATFORMS,
  STATUS_VERIFIED,
  STATUS_MANUAL,
  STATUS_WRONG,
  STATUS_NOT_FOUND,
} from "@/lib/results-mapper";
import type { ResultRow } from "@/types/results";

/**
 * Loads the latest run for the analysis view, in the browser.
 *
 * See ResultsClient for why this cannot be done server-side: the bearer token
 * lives in localStorage and the export files live on the API's disk, not this
 * one's. Client-side loading also makes the static export possible.
 */
function useAnalysisRows(jobId: string) {
  const [rows, setRows] = useState<ResultRow[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;

    void (async () => {
      if (!getPythonApiUrl()) {
        if (alive) {
          setLoadError("NEXT_PUBLIC_PYTHON_API_URL is not set.");
          setLoading(false);
        }
        return;
      }
      try {
        // Respect the run selected from History; otherwise the latest run.
        const path = jobId
          ? `/api/results/latest?job_id=${encodeURIComponent(jobId)}`
          : "/api/results/latest";
        const res = await authedFetch(path, { cache: "no-store" });
        if (!alive) return;
        if (!res.ok) {
          setLoadError(`Could not load results (HTTP ${res.status}).`);
          setLoading(false);
          return;
        }
        const data = (await res.json()) as { rows?: Record<string, unknown>[] };
        if (alive) setRows((data.rows ?? []).map((r) => mapRecordToRow(r)));
      } catch (err) {
        if (alive) setLoadError(err instanceof Error ? err.message : "Could not reach the API.");
      } finally {
        if (alive) setLoading(false);
      }
    })();

    return () => {
      alive = false;
    };
  }, [jobId]);

  return { rows, loading, loadError };
}

export function AnalysisClient() {
  const searchParams = useSearchParams();
  const jobId = searchParams.get("job") ?? "";
  const { rows, loading, loadError } = useAnalysisRows(jobId);
  // Tag by STATUS, not confidence. The three scored buckets (Verified / Needs
  // Manual Review / Wrong) are the accuracy denominator. Not Found is a real
  // answer ("account confirmed absent") shown separately and EXCLUDED from the
  // score; Not Checked (search was unavailable) is ignored entirely.
  const statuses = rows.flatMap((r) => RESULT_PLATFORMS.map((p) => r.platforms[p.key].status));
  const verifiedCount = statuses.filter((s) => s === STATUS_VERIFIED).length;
  const manualCount = statuses.filter((s) => s === STATUS_MANUAL).length;
  const wrongCount = statuses.filter((s) => s === STATUS_WRONG).length;
  const notFoundCount = statuses.filter((s) => s === STATUS_NOT_FOUND).length;

  const scored = verifiedCount + manualCount + wrongCount; // accuracy denominator
  const attempted = scored + notFoundCount; // for the Not Found share only

  const vDeg = scored ? (verifiedCount / scored) * 360 : 0;
  const mDeg = scored ? (manualCount / scored) * 360 : 0;
  const chartStyle = {
    background:
      scored > 0
        ? `conic-gradient(
          rgba(16,185,129,0.95) 0deg ${vDeg}deg,
          rgba(245,158,11,0.95) ${vDeg}deg ${vDeg + mDeg}deg,
          rgba(244,63,94,0.95) ${vDeg + mDeg}deg 360deg
        )`
        : "conic-gradient(rgba(71,85,105,0.5) 0deg 360deg)",
  };

  const pctScored = (v: number) => (scored ? `${((v / scored) * 100).toFixed(1)}%` : "0.0%");
  const notFoundPct = attempted ? `${((notFoundCount / attempted) * 100).toFixed(1)}%` : "0.0%";

  return (
    <div className="relative flex min-h-screen flex-col md:flex-row">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_0%_0%,rgba(242,209,0,0.06),transparent_32%)]"
      />
      <AppSidebar />

      <AppMain className="relative z-10 flex-1 p-4 pb-24 md:p-8 md:pb-8">
        <AppPageHeader
          title="Analysis"
          subtitle="Status breakdown"
          icon="donut_large"
          actions={
            <Link href={jobId ? `/results?job=${encodeURIComponent(jobId)}` : "/results"} className="lf-btn-secondary inline-flex items-center gap-2 px-4 py-2.5 text-sm">
              <span className="material-symbols-outlined text-base">arrow_back</span>
              Back to Results
            </Link>
          }
        />

        <div className="mx-auto max-w-6xl space-y-8">
          {loading && (
            <p className="lf-card p-4 text-sm text-slate-400" aria-busy="true">
              Loading the latest run…
            </p>
          )}
          {loadError && (
            <p className="lf-card p-4 text-sm text-amber-400" role="alert">
              {loadError}
            </p>
          )}
          <section className="grid grid-cols-1 gap-8 lg:grid-cols-2">
            <div className="lf-enter lf-card lf-card-hover flex min-h-[420px] items-center justify-center p-6 lg:min-h-[500px]">
              <div className="relative">
                <div
                  className="relative h-[320px] w-[320px] rounded-full p-5 shadow-2xl shadow-black/40 ring-1 ring-white/10 sm:h-[360px] sm:w-[360px]"
                  style={chartStyle}
                >
                  <div className="absolute inset-[18%] flex items-center justify-center rounded-full bg-slate-950 ring-1 ring-white/10">
                    <div className="text-center">
                      <div className="inline-flex items-center gap-1 text-xs font-semibold uppercase tracking-widest text-slate-400">
                        <span className="material-symbols-outlined text-sm">link</span>
                        Scored Links
                      </div>
                      <div className="mt-1 text-4xl font-black text-slate-100">{scored}</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div className="space-y-4">
              <AnalysisRow
                label="Verified"
                count={verifiedCount}
                pct={pctScored(verifiedCount)}
                tone="emerald"
                icon="check_circle"
              />
              <AnalysisRow
                label="Need Manual Review"
                count={manualCount}
                pct={pctScored(manualCount)}
                tone="amber"
                icon="warning"
              />
              <AnalysisRow
                label="Wrong"
                count={wrongCount}
                pct={pctScored(wrongCount)}
                tone="rose"
                icon="cancel"
              />
              <AnalysisRow
                label="Not Found"
                count={notFoundCount}
                pct={notFoundPct}
                tone="slate"
                icon="do_not_disturb_on"
              />
              <div className="lf-enter lf-enter-delay-2 lf-card mt-4 flex items-start gap-3 px-4 py-4 text-sm text-slate-300">
                <span className="material-symbols-outlined text-primary">insights</span>
                Verified, Needs Manual Review and Wrong are scored out of the resolved links.
                Not Found (accounts confirmed absent) is shown separately and excluded from the score.
              </div>
            </div>
          </section>
        </div>
      </AppMain>

      <AppMobileNav />
    </div>
  );
}

function AnalysisRow({
  label,
  count,
  pct,
  tone,
  icon,
}: {
  label: string;
  count: number;
  pct: string;
  tone: "emerald" | "amber" | "rose" | "slate";
  icon: string;
}) {
  const cls =
    tone === "emerald"
      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200 lf-stat-glow-emerald"
      : tone === "amber"
        ? "border-amber-500/30 bg-amber-500/10 text-amber-200 lf-stat-glow-amber"
        : tone === "rose"
          ? "border-rose-500/30 bg-rose-500/10 text-rose-200"
          : "border-slate-500/30 bg-slate-500/10 text-slate-300";

  return (
    <div className={`lf-enter lf-card-hover flex items-center justify-between rounded-xl border px-4 py-4 ${cls}`}>
      <span className="inline-flex items-center gap-2 text-sm font-semibold">
        <span className="material-symbols-outlined text-base">{icon}</span>
        {label}
      </span>
      <span className="text-sm font-bold">
        {count} <span className="opacity-80">({pct})</span>
      </span>
    </div>
  );
}
