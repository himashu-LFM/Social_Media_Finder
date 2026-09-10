"use client";

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AppShell, StageStrip } from "@/components/AppShell";
import { ResultsAnalysisButton } from "@/components/ResultsAnalysisButton";
import { ResultsExportButton } from "@/components/ResultsExportButton";
import { ResultsTable } from "@/components/ResultsTable";
import { authedFetch } from "@/lib/auth";
import { checkDbHealth, type DbHealth } from "@/lib/decisions";
import { getPythonApiUrl } from "@/lib/processing-job";
import { mapRecordToRow, RESULT_PLATFORMS, STATUS_MANUAL } from "@/lib/results-mapper";
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
  const [db, setDb] = useState<DbHealth | null>(null);

  useEffect(() => {
    let alive = true;
    void checkDbHealth().then((h) => {
      if (alive) setDb(h);
    });
    return () => {
      alive = false;
    };
  }, []);

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

  const manualCount = useMemo(
    () =>
      rows.reduce(
        (n, r) =>
          n + RESULT_PLATFORMS.filter((p) => r.platforms[p.key].status === STATUS_MANUAL).length,
        0,
      ),
    [rows],
  );

  const stageNote = loading
    ? "Loading the latest run…"
    : `${rows.length} rows · ${rows.length * RESULT_PLATFORMS.length} cells · ${manualCount} need review`;

  return (
    <AppShell
      icon="table_chart"
      eyebrow="Verification output"
      title="Results"
      stages={<StageStrip current="score" noteStrong note={stageNote} />}
      actions={
        <>
          <span className={`sc-pill ${db?.connected ? "sc-tone-good" : "sc-tone-mute"}`}>
            <span
              className="material-symbols-outlined"
              style={{ fontSize: 14, color: "var(--tone)" }}
            >
              database
            </span>
            {db?.connected ? "DB connected" : "DB offline"}
          </span>
          <ResultsAnalysisButton jobId={jobId} />
          <ResultsExportButton rows={rows} sourceFileName={latestFileName} />
        </>
      }
    >
      {loading ? (
        <div className="sc-stack">
          <ResultsSkeleton />
        </div>
      ) : (
        <ResultsTable
          rows={rows}
          notice={
            (loadError || loadWarning) && (
              <div
                className={`sc-banner ${loadError ? "sc-tone-warn" : "sc-tone-info"}`}
                role={loadError ? "alert" : undefined}
              >
                <span className="material-symbols-outlined">
                  {loadError ? "warning" : "info"}
                </span>
                {loadError ?? loadWarning}
              </div>
            )
          }
        />
      )}
    </AppShell>
  );
}

function ResultsSkeleton() {
  return (
    <div className="sc-card" style={{ padding: 16 }} aria-busy="true" aria-label="Loading results">
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {Array.from({ length: 7 }).map((_, i) => (
          <div
            key={i}
            className="sc-pulse"
            style={{ height: 30, borderRadius: 8, background: "rgba(255,255,255,0.045)" }}
          />
        ))}
      </div>
    </div>
  );
}
