import type { Metadata } from "next";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import * as XLSX from "xlsx";
import { AppMobileNav } from "@/components/AppMobileNav";
import { AppPageHeader } from "@/components/AppPageHeader";
import { AppSidebar } from "@/components/AppSidebar";
import {
  mapRecordToRow,
  RESULT_PLATFORMS,
  statusTone,
} from "@/lib/results-mapper";
import type { PlatformResult, ResultRow } from "@/types/results";

// Companion of the Results page. Reads the Serper-only (Phase A) export — what
// Serper + the LLM produced BEFORE the Apify backup and cross-platform
// corroboration — so you can see what Serper alone brings in.
const REQUIRED_PREFIX = "Talent_Social_Serper_";
const REQUIRED_SUFFIX = ".xlsx";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Serper Result | ListenFirst",
  description: "Serper + LLM first-pass output, before the Apify backup.",
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

async function loadSerperWorkbookRows(jobId?: string): Promise<{
  rows: ResultRow[];
  latestFileName: string | null;
  loadError: string | null;
  loadWarning: string | null;
}> {
  const api = getPythonApiUrl();
  if (api) {
    try {
      const url = jobId
        ? `${api}/api/results/serper/latest?job_id=${encodeURIComponent(jobId)}`
        : `${api}/api/results/serper/latest`;
      const res = await fetch(url, { cache: "no-store" });
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
              "This run is still processing — Serper results will appear here once it finishes.",
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
          ? `Newer Serper export(s) could not be read: ${skipped.join(", ")}. Showing ${name}.`
          : null;
      return { rows, latestFileName: name, loadError: null, loadWarning };
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
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

function PlatformCell({ result }: { result: PlatformResult }) {
  const pct = Math.round(result.confidence * 100);
  const tone = statusTone(result.status);
  return (
    <div className="min-w-[160px] space-y-1.5">
      <div className="flex items-center gap-2">
        <span className={`inline-flex rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ring-1 ${tone}`}>
          {result.status || "—"}
        </span>
        {result.link && <span className="text-[10px] font-semibold text-slate-400">{pct}%</span>}
      </div>
      {result.link ? (
        <a
          href={result.link}
          target="_blank"
          rel="noreferrer"
          className="block cursor-pointer text-xs break-all text-slate-300 underline-offset-2 transition hover:text-primary hover:underline"
        >
          {result.link}
        </a>
      ) : (
        <span className="text-xs text-slate-600">No profile</span>
      )}
      {result.reason && (
        <p className="text-[10px] leading-snug text-slate-500" title={result.reason}>
          {result.reason.length > 140 ? `${result.reason.slice(0, 140)}…` : result.reason}
        </p>
      )}
    </div>
  );
}

export default async function SerperResultPage({
  searchParams,
}: {
  searchParams: Promise<{ job?: string }>;
}) {
  const sp = await searchParams;
  const { rows, latestFileName, loadError, loadWarning } = await loadSerperWorkbookRows(sp?.job);
  const verifiedCount = rows.filter((r) =>
    RESULT_PLATFORMS.some((p) => r.platforms[p.key].status === "Verified"),
  ).length;

  return (
    <div className="relative flex min-h-screen flex-col md:flex-row">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_100%_0%,rgba(242,209,0,0.06),transparent_30%)]"
      />
      <AppSidebar />

      <main className="relative z-10 flex-1 p-4 pb-32 md:ml-64 md:p-8">
        <AppPageHeader
          title="Serper Result"
          subtitle="Serper + LLM · before Apify backup"
          icon="travel_explore"
        />

        <div className="mx-auto max-w-7xl space-y-6">
          <div className="lf-enter lf-card p-4 sm:p-5">
            <div className="flex flex-wrap items-start gap-3">
              <span className="material-symbols-outlined text-primary">travel_explore</span>
              <div className="min-w-0 flex-1">
                <p className="text-sm text-slate-300">
                  This is the <span className="font-semibold text-primary">first pass only</span>:
                  what the Serper <code className="text-primary">site:</code> search + LLM
                  verification produced, <span className="font-semibold">before</span> the Apify
                  backup and cross-platform corroboration. Compare with{" "}
                  <span className="font-semibold">Results</span> to see what the backup changed.
                </p>
                <p className="mt-2 text-xs text-slate-500">
                  Latest file: {latestFileName ?? "No Talent_Social_Serper_*.xlsx found yet"}
                </p>
                {loadWarning && <p className="mt-1 text-xs text-sky-400">{loadWarning}</p>}
                {loadError && (
                  <p className="mt-1 text-xs text-amber-400">
                    Could not read any Serper workbook: {loadError}
                  </p>
                )}
              </div>
            </div>
          </div>

          <div className="lf-enter lf-enter-delay-1 lf-card overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1400px] border-collapse text-left">
                <thead>
                  <tr className="border-b border-white/10 bg-slate-950/70">
                    {["Talent Name", "Wikipedia URL", ...RESULT_PLATFORMS.map((p) => p.label), "Confidence"].map(
                      (h) => (
                        <th
                          key={h}
                          className="px-4 py-4 text-xs font-bold uppercase tracking-wider text-slate-500"
                        >
                          {h}
                        </th>
                      ),
                    )}
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/8">
                  {rows.map((r, i) => (
                    <tr
                      key={`${r.name}-${i}`}
                      className="align-top transition-colors hover:bg-primary/[0.03]"
                    >
                      <td className="px-4 py-4 font-semibold text-slate-100">{r.name}</td>
                      <td className="px-4 py-4 text-sm">
                        {r.wikipediaUrl ? (
                          <a
                            href={r.wikipediaUrl}
                            target="_blank"
                            rel="noreferrer"
                            className="cursor-pointer break-all text-xs text-slate-400 underline-offset-2 hover:text-primary hover:underline"
                          >
                            {r.wikipediaUrl}
                          </a>
                        ) : (
                          <span className="text-slate-600">-</span>
                        )}
                      </td>
                      {RESULT_PLATFORMS.map((p) => (
                        <td key={p.key} className="px-4 py-4">
                          <PlatformCell result={r.platforms[p.key]} />
                        </td>
                      ))}
                      <td className="px-4 py-4 text-sm font-bold text-slate-200">
                        {Math.round(r.confidence * 100)}%
                      </td>
                    </tr>
                  ))}
                  {rows.length === 0 && (
                    <tr>
                      <td
                        colSpan={RESULT_PLATFORMS.length + 3}
                        className="px-6 py-10 text-center text-sm text-slate-500"
                      >
                        No Serper output yet. Run the pipeline to generate a
                        `Talent_Social_Serper_*.xlsx` file.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
            <div className="lf-enter lf-enter-delay-2 lf-card p-6">
              <div className="text-xs font-bold uppercase tracking-wider text-slate-500">
                Talent rows
              </div>
              <div className="mt-2 text-4xl font-black text-slate-100">{rows.length}</div>
            </div>
            <div className="lf-enter lf-enter-delay-2 lf-card lf-stat-glow-emerald p-6">
              <div className="text-xs font-bold uppercase tracking-wider text-slate-500">
                Rows with a Serper-Verified platform
              </div>
              <div className="mt-2 text-4xl font-black text-emerald-400">{verifiedCount}</div>
            </div>
          </div>
        </div>
      </main>

      <AppMobileNav />
    </div>
  );
}
