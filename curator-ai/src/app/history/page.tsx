"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AppMobileNav } from "@/components/AppMobileNav";
import { AppPageHeader } from "@/components/AppPageHeader";
import { AppSidebar } from "@/components/AppSidebar";
import { statusTone } from "@/lib/results-mapper";
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

const runTone: Record<string, string> = {
  completed: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30",
  running: "bg-primary/10 text-primary ring-primary/30",
  cancelling: "bg-amber-500/10 text-amber-300 ring-amber-500/30",
  cancelled: "bg-sky-500/10 text-sky-300 ring-sky-500/30",
  failed: "bg-rose-500/10 text-rose-300 ring-rose-500/30",
  queued: "bg-slate-500/10 text-slate-300 ring-slate-500/30",
};

export default function HistoryPage() {
  const [tab, setTab] = useState<Tab>("runs");
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
  const empty = isRuns ? runs.length === 0 : uploads.length === 0;

  return (
    <div className="relative flex min-h-screen flex-col md:flex-row">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_100%_0%,rgba(242,209,0,0.06),transparent_30%)]"
      />
      <AppSidebar />

      <main className="relative z-10 flex-1 p-4 pb-32 md:ml-64 md:p-8">
        <AppPageHeader title="History" subtitle="Uploads & runs" icon="history" />

        <div className="mx-auto max-w-6xl space-y-5">
          {/* Tabs */}
          <div className="inline-flex rounded-xl border border-white/10 bg-slate-950/60 p-1">
            {(["runs", "uploads"] as Tab[]).map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setTab(t)}
                className={`inline-flex cursor-pointer items-center gap-2 rounded-lg px-4 py-2 text-sm font-bold capitalize transition ${
                  tab === t ? "bg-primary/15 text-primary" : "text-slate-400 hover:text-slate-200"
                }`}
              >
                <span className="material-symbols-outlined text-lg">
                  {t === "runs" ? "playlist_add_check" : "upload_file"}
                </span>
                {t === "runs" ? `Runs (${runs.length})` : `Uploads (${uploads.length})`}
              </button>
            ))}
          </div>

          {error && (
            <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
              {error}
            </div>
          )}

          <div className="lf-card overflow-hidden">
            <div className="overflow-x-auto">
              {isRuns ? (
                <table className="w-full min-w-[820px] border-collapse text-left text-sm">
                  <thead>
                    <tr className="border-b border-white/10 bg-slate-950/70 text-[10.5px] uppercase tracking-widest text-slate-500">
                      <th className="px-4 py-3 font-bold">When</th>
                      <th className="px-4 py-3 font-bold">Source</th>
                      <th className="px-4 py-3 font-bold">Rows</th>
                      <th className="px-4 py-3 font-bold">Status</th>
                      <th className="px-4 py-3 font-bold">By</th>
                      <th className="px-4 py-3 font-bold">Results</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/[0.06]">
                    {runs.map((r) => (
                      <tr key={r.id} className="transition-colors hover:bg-white/[0.02]">
                        <td className="whitespace-nowrap px-4 py-3 text-slate-300" title={r.created_at}>
                          {timeAgo(r.created_at)}
                        </td>
                        <td className="max-w-[220px] truncate px-4 py-3 text-slate-300">
                          {r.source_filename || "Names entered manually"}
                        </td>
                        <td className="px-4 py-3 tabular-nums text-slate-400">{r.row_count}</td>
                        <td className="px-4 py-3">
                          <span
                            className={`inline-flex rounded-full px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wide ring-1 ${
                              runTone[r.status] ?? runTone.queued
                            }`}
                          >
                            {r.status}
                          </span>
                          {r.error && (
                            <span className="ml-2 text-[10px] text-rose-400" title={r.error}>
                              {r.error.slice(0, 40)}
                            </span>
                          )}
                        </td>
                        <td className="max-w-[160px] truncate px-4 py-3 text-slate-400">
                          {r.started_by_name || r.started_by_email || "—"}
                        </td>
                        <td className="px-4 py-3">
                          {r.result_rows > 0 ? (
                            <Link
                              href={`/results?job=${encodeURIComponent(r.id)}`}
                              className="inline-flex cursor-pointer items-center gap-1 text-xs font-semibold text-primary hover:underline"
                            >
                              View {r.result_rows}
                              <span className="material-symbols-outlined text-sm">arrow_forward</span>
                            </Link>
                          ) : (
                            <span className="text-xs text-slate-600">—</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <table className="w-full min-w-[720px] border-collapse text-left text-sm">
                  <thead>
                    <tr className="border-b border-white/10 bg-slate-950/70 text-[10.5px] uppercase tracking-widest text-slate-500">
                      <th className="px-4 py-3 font-bold">When</th>
                      <th className="px-4 py-3 font-bold">Filename</th>
                      <th className="px-4 py-3 font-bold">Size</th>
                      <th className="px-4 py-3 font-bold">Rows</th>
                      <th className="px-4 py-3 font-bold">By</th>
                      <th className="px-4 py-3 font-bold">Run</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/[0.06]">
                    {uploads.map((u) => (
                      <tr key={u.id} className="transition-colors hover:bg-white/[0.02]">
                        <td className="whitespace-nowrap px-4 py-3 text-slate-300" title={u.created_at}>
                          {timeAgo(u.created_at)}
                        </td>
                        <td className="max-w-[260px] truncate px-4 py-3 font-medium text-slate-200">
                          {u.filename}
                        </td>
                        <td className="whitespace-nowrap px-4 py-3 tabular-nums text-slate-400">
                          {fmtBytes(u.size_bytes)}
                        </td>
                        <td className="px-4 py-3 tabular-nums text-slate-400">{u.row_count ?? "—"}</td>
                        <td className="max-w-[160px] truncate px-4 py-3 text-slate-400">
                          {u.uploaded_by_name || u.uploaded_by_email || "—"}
                        </td>
                        <td className="px-4 py-3">
                          {u.job_id ? (
                            <Link
                              href={`/results?job=${encodeURIComponent(u.job_id)}`}
                              className={`inline-flex rounded-full px-2 py-0.5 text-[10px] font-bold uppercase ring-1 ${
                                statusTone(u.job_status ?? "")
                              }`}
                            >
                              {u.job_status || "view"}
                            </Link>
                          ) : (
                            <span className="text-xs text-slate-600">—</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              {loading && (
                <div className="px-6 py-12 text-center text-sm text-slate-500">Loading history…</div>
              )}
              {!loading && empty && (
                <div className="px-6 py-14 text-center">
                  <span className="material-symbols-outlined mb-2 block text-3xl text-slate-700">
                    inbox
                  </span>
                  <p className="text-sm text-slate-500">
                    No {tab} yet. {isRuns ? "Run the pipeline" : "Upload a file"} to see it here.
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>
      </main>

      <AppMobileNav />
    </div>
  );
}
