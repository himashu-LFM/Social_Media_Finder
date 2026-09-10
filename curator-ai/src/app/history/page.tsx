"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { authedFetch } from "@/lib/auth";

type UploadRow = {
  id: number;
  job_id: string | null;
  filename: string;
  size_bytes: number | null;
  row_count: number | null;
  created_at: string;
  uploaded_by_name: string | null;
  uploaded_by_email: string | null;
  job_status: string | null;
};

type RunRow = {
  id: string;
  status: string;
  source_filename: string | null;
  created_at: string;
  row_count: number;
  result_rows: number;
  started_by_name: string | null;
  started_by_email: string | null;
  error: string | null;
};

type Tab = "runs" | "uploads";

function timeAgo(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const s = Math.round((Date.now() - then) / 1000);
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

function fmtBytes(n: number | null): string {
  if (!n) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** Run lifecycle → tone class. Keys match the API's own status strings. */
const RUN_TONE: Record<string, string> = {
  completed: "sc-tone-good",
  running: "sc-tone-live",
  cancelling: "sc-tone-warn",
  cancelled: "sc-tone-info",
  failed: "sc-tone-bad",
  queued: "sc-tone-mute",
};

const runTone = (status: string) => RUN_TONE[status] ?? RUN_TONE.queued;

export default function HistoryPage() {
  const [tab, setTab] = useState<Tab>("runs");
  const [filter, setFilter] = useState("");
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [uploads, setUploads] = useState<UploadRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void (async () => {
      setLoading(true);
      setError(null);
      try {
        const [r, u] = await Promise.all([
          authedFetch("/api/history/runs?limit=200"),
          authedFetch("/api/history/uploads?limit=200"),
        ]);
        if (!alive) return;
        if (r.ok) setRuns(((await r.json()) as { runs: RunRow[] }).runs ?? []);
        if (u.ok) setUploads(((await u.json()) as { uploads: UploadRow[] }).uploads ?? []);
        if (!r.ok && !u.ok) setError("Could not load history. Is the database configured?");
      } catch {
        if (alive) setError("Could not reach the API.");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const isRuns = tab === "runs";
  const q = filter.trim().toLowerCase();

  // The design shows this filter in the header; here it actually filters, and
  // it matches the person as well as the file because that is the other way
  // an analyst looks for their own run.
  const visibleRuns = useMemo(
    () =>
      !q
        ? runs
        : runs.filter((r) =>
            [r.source_filename, r.started_by_name, r.started_by_email]
              .some((v) => v?.toLowerCase().includes(q)),
          ),
    [runs, q],
  );
  const visibleUploads = useMemo(
    () =>
      !q
        ? uploads
        : uploads.filter((u) =>
            [u.filename, u.uploaded_by_name, u.uploaded_by_email]
              .some((v) => v?.toLowerCase().includes(q)),
          ),
    [uploads, q],
  );

  const runCounts = useMemo(() => {
    const c = { completed: 0, cancelled: 0, failed: 0 };
    for (const r of runs) if (r.status in c) c[r.status as keyof typeof c] += 1;
    return c;
  }, [runs]);

  const empty = isRuns ? visibleRuns.length === 0 : visibleUploads.length === 0;

  return (
    <AppShell
      icon="history"
      eyebrow="Uploads & runs"
      title="History"
      actions={
        <>
          <span className="sc-search">
            <span className="material-symbols-outlined">search</span>
            <input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter by file or person…"
              aria-label="Filter history"
            />
          </span>
          <Link href="/discovery" className="sc-btn">
            <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
              add
            </span>
            New run
          </Link>
        </>
      }
      stages={
        <div className="sc-subbar">
          <span className="sc-tabs" role="tablist" aria-label="History view">
            <button
              type="button"
              role="tab"
              aria-selected={isRuns}
              onClick={() => setTab("runs")}
            >
              <span className="material-symbols-outlined" style={{ fontSize: 15 }}>
                playlist_add_check
              </span>
              Runs ({runs.length})
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={!isRuns}
              onClick={() => setTab("uploads")}
            >
              <span className="material-symbols-outlined" style={{ fontSize: 15 }}>
                upload_file
              </span>
              Uploads ({uploads.length})
            </button>
          </span>
          <span className="sc-subbar-note">
            {isRuns
              ? "One row per pipeline run — open any finished run in Results"
              : "Every file uploaded, newest first"}
          </span>
        </div>
      }
    >
      <div className="sc-stack">
        {error && (
          <div className="sc-banner sc-tone-warn" role="alert">
            <span className="material-symbols-outlined">warning</span>
            {error}
          </div>
        )}

        <section className="sc-card" style={{ overflow: "hidden" }}>
          <div className="sc-data-wrap">
            {isRuns ? (
              <table className="sc-data" style={{ minWidth: 780 }}>
                <thead>
                  <tr>
                    <th style={{ width: 92 }}>When</th>
                    <th>Source</th>
                    <th className="num" style={{ width: 62 }}>
                      Rows
                    </th>
                    <th style={{ width: 118 }}>Status</th>
                    <th>By</th>
                    <th style={{ width: 110 }}>Results</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleRuns.map((r) => (
                    <tr key={r.id}>
                      <td className="when" title={r.created_at}>
                        {timeAgo(r.created_at)}
                      </td>
                      <td className="trunc">{r.source_filename || "Names entered manually"}</td>
                      <td className="num">{r.row_count}</td>
                      <td>
                        <span className={`sc-status ${runTone(r.status)}`}>{r.status}</span>
                        {r.error && (
                          <span
                            style={{ marginLeft: 6, fontSize: 10, color: "#fb7185" }}
                            title={r.error}
                          >
                            {r.error.slice(0, 40)}
                          </span>
                        )}
                      </td>
                      <td className="trunc narrow">
                        {r.started_by_name || r.started_by_email || "—"}
                      </td>
                      <td>
                        {r.result_rows > 0 ? (
                          <Link
                            href={`/results?job=${encodeURIComponent(r.id)}`}
                            className="sc-link"
                          >
                            View {r.result_rows}
                            <span className="material-symbols-outlined" style={{ fontSize: 14 }}>
                              arrow_forward
                            </span>
                          </Link>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <table className="sc-data" style={{ minWidth: 720 }}>
                <thead>
                  <tr>
                    <th style={{ width: 92 }}>When</th>
                    <th>Filename</th>
                    <th className="num" style={{ width: 78 }}>
                      Size
                    </th>
                    <th className="num" style={{ width: 62 }}>
                      Rows
                    </th>
                    <th>By</th>
                    <th style={{ width: 110 }}>Run</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleUploads.map((u) => (
                    <tr key={u.id}>
                      <td className="when" title={u.created_at}>
                        {timeAgo(u.created_at)}
                      </td>
                      <td className="trunc" style={{ fontWeight: 600 }}>
                        {u.filename}
                      </td>
                      <td className="num">{fmtBytes(u.size_bytes)}</td>
                      <td className="num">{u.row_count ?? "—"}</td>
                      <td className="trunc narrow">
                        {u.uploaded_by_name || u.uploaded_by_email || "—"}
                      </td>
                      <td>
                        {u.job_id ? (
                          <Link
                            href={`/results?job=${encodeURIComponent(u.job_id)}`}
                            className={`sc-status ${runTone(u.job_status ?? "")}`}
                          >
                            {u.job_status || "view"}
                          </Link>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            {loading && <div className="sc-empty">Loading history…</div>}
            {!loading && empty && (
              <div className="sc-empty">
                <span className="material-symbols-outlined">
                  {q ? "filter_alt_off" : "inbox"}
                </span>
                {q
                  ? `Nothing matches “${filter.trim()}”.`
                  : `No ${tab} yet. ${isRuns ? "Run the pipeline" : "Upload a file"} to see it here.`}
              </div>
            )}
          </div>

          <div className="sc-foot">
            {isRuns ? (
              <>
                <span style={{ whiteSpace: "nowrap" }}>
                  Showing <strong>{visibleRuns.length}</strong> of {runs.length} runs
                </span>
                <span
                  style={{
                    display: "inline-flex",
                    flexWrap: "wrap",
                    alignItems: "center",
                    gap: 14,
                  }}
                >
                  <span className="sc-foot-item sc-tone-good">
                    <span className="sc-dot" />
                    completed {runCounts.completed}
                  </span>
                  <span className="sc-foot-item sc-tone-info">
                    <span className="sc-dot" />
                    cancelled {runCounts.cancelled}
                  </span>
                  <span className="sc-foot-item sc-tone-bad">
                    <span className="sc-dot" />
                    failed {runCounts.failed}
                  </span>
                </span>
              </>
            ) : (
              <>
                <span style={{ whiteSpace: "nowrap" }}>
                  Showing <strong>{visibleUploads.length}</strong> of {uploads.length} uploads
                </span>
                <span style={{ whiteSpace: "nowrap" }}>
                  Empty rows are skipped at upload time
                </span>
              </>
            )}
          </div>
        </section>
      </div>
    </AppShell>
  );
}
