import type { Metadata } from "next";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import * as XLSX from "xlsx";
import { AppMobileNav } from "@/components/AppMobileNav";
import { AppPageHeader } from "@/components/AppPageHeader";
import { ResultsAnalysisButton } from "@/components/ResultsAnalysisButton";
import { AppSidebar } from "@/components/AppSidebar";
import { ResultsExportButton } from "@/components/ResultsExportButton";
import { ResultsTable } from "@/components/ResultsTable";
import { mapRecordToRow } from "@/lib/results-mapper";
import type { ResultRow } from "@/types/results";

const REQUIRED_PREFIX = "Talent_Social_Lookup_";
const REQUIRED_SUFFIX = ".xlsx";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Results | ListenFirst",
  description: "Verified social profile output aligned with the python export schema.",
};

function getPythonApiUrl(): string | null {
  const u = process.env.NEXT_PUBLIC_PYTHON_API_URL?.trim().replace(/\/$/, "");
  return u && u.length > 0 ? u : null;
}

function readRowsFromWorkbook(wb: XLSX.WorkBook): ResultRow[] {
  const firstSheetName = wb.SheetNames[0];
  if (!firstSheetName) return [];

  const sheet = wb.Sheets[firstSheetName];
  const jsonRows = XLSX.utils.sheet_to_json<Record<string, unknown>>(sheet, {
    defval: "",
  });

  return jsonRows.map((r) => mapRecordToRow(r));
}

async function readRowsFromPathWithRetry(fullPath: string): Promise<ResultRow[]> {
  let lastErr = "";
  for (let i = 0; i < 12; i++) {
    try {
      const buf = await readFile(fullPath);
      const wb = XLSX.read(buf, { type: "buffer", cellDates: false });
      return readRowsFromWorkbook(wb);
    } catch (error) {
      lastErr = error instanceof Error ? error.message : String(error);
      await new Promise((r) => setTimeout(r, 400));
    }
  }
  throw new Error(lastErr || "Unknown read error");
}

async function loadLatestWorkbookRows(jobId?: string): Promise<{
  rows: ResultRow[];
  latestFileName: string | null;
  loadError: string | null;
  loadWarning: string | null;
}> {
  const api = getPythonApiUrl();
  if (api) {
    try {
      const url = jobId
        ? `${api}/api/results/latest?job_id=${encodeURIComponent(jobId)}`
        : `${api}/api/results/latest`;
      const res = await fetch(url, {
        cache: "no-store",
      });
      if (res.ok) {
        const data = (await res.json()) as {
          rows?: Record<string, unknown>[];
          filename?: string | null;
          warning?: string | null;
          error?: string | null;
          pending?: boolean;
        };
        if (data.pending) {
          return {
            rows: [],
            latestFileName: null,
            loadError: null,
            loadWarning:
              "This run is still processing — results will appear here once it finishes.",
          };
        }
        if (data.rows && data.rows.length > 0) {
          return {
            rows: data.rows.map((r) => mapRecordToRow(r)),
            latestFileName: data.filename ?? null,
            loadError: data.error ?? null,
            loadWarning: data.warning ?? null,
          };
        }
      }
    } catch {
      /* fall back to local read */
    }
  }

  const dataDir = path.resolve(process.cwd(), "..");
  const files = await readdir(dataDir, { withFileTypes: true });
  const candidates = files
    .filter((f) => f.isFile())
    .map((f) => f.name)
    .filter(
      (n) =>
        n.startsWith(REQUIRED_PREFIX) &&
        n.endsWith(REQUIRED_SUFFIX) &&
        !n.startsWith(".~lock"),
    )
    .sort()
    .reverse();

  if (candidates.length === 0) {
    return { rows: [], latestFileName: null, loadError: null, loadWarning: null };
  }

  const skipped: string[] = [];
  let lastError = "";

  for (const name of candidates) {
    try {
      const fullPath = path.join(dataDir, name);
      const rows = await readRowsFromPathWithRetry(fullPath);
      const loadWarning =
        skipped.length > 0
          ? `Newer export(s) could not be read (often open in Excel): ${skipped.join(", ")}. Showing ${name}.`
          : null;
      return { rows, latestFileName: name, loadError: null, loadWarning };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      lastError = message;
      skipped.push(name);
    }
  }

  return {
    rows: [],
    latestFileName: candidates[0],
    loadError: lastError || "Unknown read error",
    loadWarning: null,
  };
}

export default async function ResultsPage({
  searchParams,
}: {
  searchParams: Promise<{ job?: string }>;
}) {
  const sp = await searchParams;
  const { rows, latestFileName, loadError, loadWarning } = await loadLatestWorkbookRows(sp?.job);
  const totalRows = rows.length;
  const highCount = rows.filter((r) => r.confidence > 0.8).length;
  const ambiguousCount = rows.filter(
    (r) => r.confidence >= 0.5 && r.confidence <= 0.8,
  ).length;
  const avgConfidence =
    totalRows === 0
      ? 0
      : rows.reduce((acc, r) => acc + r.confidence, 0) / totalRows;

  const apiHint = getPythonApiUrl()
    ? null
    : "Tip: set NEXT_PUBLIC_PYTHON_API_URL to your FastAPI URL so Results reads exports via Python when Excel locks the file.";

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
                  Latest file: {latestFileName ?? "No Talent_Social_Lookup_*.xlsx found yet"}
                </p>
                {loadWarning && <p className="mt-1 text-xs text-sky-400">{loadWarning}</p>}
                {loadError && (
                  <p className="mt-1 text-xs text-amber-400">
                    Could not read any workbook (possibly all open/locked): {loadError}
                  </p>
                )}
                {apiHint && <p className="mt-2 text-xs text-slate-500">{apiHint}</p>}
              </div>
            </div>
          </div>

          <div className="lf-enter lf-enter-delay-1">
            <ResultsTable rows={rows} />
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
