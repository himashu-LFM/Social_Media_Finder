import type { Metadata } from "next";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import * as XLSX from "xlsx";
import { AppMobileNav } from "@/components/AppMobileNav";
import { AppPageHeader } from "@/components/AppPageHeader";
import { ResultsAnalysisButton } from "@/components/ResultsAnalysisButton";
import { AppSidebar } from "@/components/AppSidebar";
import { ResultsExportButton } from "@/components/ResultsExportButton";
import type { ResultRow } from "@/types/results";

const REQUIRED_PREFIX = "Talent_Social_Lookup_";
const REQUIRED_SUFFIX = ".xlsx";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Results | ListenFirst",
  description: "Profile lookup output table aligned with python export schema.",
};

function getPythonApiUrl(): string | null {
  const u = process.env.NEXT_PUBLIC_PYTHON_API_URL?.trim().replace(/\/$/, "");
  return u && u.length > 0 ? u : null;
}

function asString(v: unknown): string {
  if (v === undefined || v === null) return "";
  return String(v).trim();
}

function asConfidence(v: unknown): number {
  if (typeof v === "number") return Math.max(0, Math.min(1, v));
  const raw = String(v ?? "").trim();
  if (!raw) return 0;
  const cleaned = raw.replace(/,/g, "").replace(/%$/, "");
  const n = Number(cleaned);
  if (!Number.isFinite(n)) return 0;
  // Treat 0..1 as already-normalized, 1..100 as percent points.
  if (n > 1) return Math.max(0, Math.min(1, n / 100));
  return Math.max(0, Math.min(1, n));
}

function mapRecordToRow(r: Record<string, unknown>): ResultRow {
  return {
    name: asString(r["Talent Name"]),
    category: asString(r["title_category"] || r["de_category"] || r["category"]),
    subCategory: asString(r["title_sub_category"] || r["sub_category"]),
    facebook: asString(r["Facebook"]),
    facebookConfidence: asConfidence(r["Facebook Confidence"]),
    instagram: asString(r["Instagram"]),
    instagramConfidence: asConfidence(r["Instagram Confidence"]),
    x: asString(r["X"]),
    xConfidence: asConfidence(r["X Confidence"]),
    tiktok: asString(r["TikTok"]),
    tiktokConfidence: asConfidence(r["TikTok Confidence"]),
    youtube: asString(r["YouTube"]),
    youtubeConfidence: asConfidence(r["YouTube Confidence"]),
    confidence: asConfidence(r["Confidence"]),
    source: asString(r["Source"]),
  };
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

async function loadLatestWorkbookRows(): Promise<{
  rows: ResultRow[];
  latestFileName: string | null;
  loadError: string | null;
  loadWarning: string | null;
}> {
  const api = getPythonApiUrl();
  if (api) {
    try {
      const res = await fetch(`${api}/api/results/latest`, {
        cache: "no-store",
      });
      if (res.ok) {
        const data = (await res.json()) as {
          rows?: Record<string, unknown>[];
          filename?: string | null;
          warning?: string | null;
          error?: string | null;
        };
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

function ConfBadge({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  let cls = "bg-rose-500/10 text-rose-400 ring-rose-500/20";
  if (value > 0.8) cls = "bg-emerald-500/10 text-emerald-400 ring-emerald-500/20";
  else if (value >= 0.5) cls = "bg-amber-500/10 text-amber-400 ring-amber-500/20";
  return <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-bold ring-1 ${cls}`}>{pct}%</span>;
}

function LinkCell({ href, confidence }: { href: string; confidence: number }) {
  if (!href) return <span className="text-slate-600">-</span>;
  const pct = Math.round(confidence * 100);
  const cls =
    pct > 85
      ? "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30"
      : pct >= 70
        ? "bg-amber-500/10 text-amber-300 ring-amber-500/30"
        : "bg-rose-500/10 text-rose-300 ring-rose-500/30";
  return (
    <div className={`rounded-md px-2 py-1 ring-1 ${cls}`}>
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        className="cursor-pointer text-xs break-all underline-offset-2 transition hover:underline"
      >
        {href}
      </a>
      <div className="mt-1 text-[10px] font-semibold opacity-90">{pct}%</div>
    </div>
  );
}

export default async function ResultsPage() {
  const { rows, latestFileName, loadError, loadWarning } = await loadLatestWorkbookRows();
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
    <div className="relative flex min-h-screen flex-col bg-background md:flex-row">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_100%_0%,rgba(242,209,0,0.06),transparent_30%)]"
      />
      <AppSidebar />

      <main className="relative z-10 flex-1 p-4 pb-32 md:ml-64 md:p-8">
        <AppPageHeader
          title="Results"
          subtitle="Export output"
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
                  Output schema: Talent Name, categories, five platform links + confidences, overall
                  Confidence, Source.
                </p>
                <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
                  <span className="text-slate-400">Link confidence:</span>
                  <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 font-semibold text-emerald-300">
                    Green &gt; 85%
                  </span>
                  <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 font-semibold text-amber-300">
                    Yellow 70%-85%
                  </span>
                  <span className="rounded-full border border-rose-500/30 bg-rose-500/10 px-2 py-0.5 font-semibold text-rose-300">
                    Red &lt; 70%
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

          <div className="lf-enter lf-enter-delay-1 lf-card overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1300px] border-collapse text-left">
                <thead>
                  <tr className="border-b border-white/10 bg-slate-950/70">
                    {[
                      "Talent Name",
                      "Category",
                      "Sub Category",
                      "Facebook",
                      "Instagram",
                      "X",
                      "TikTok",
                      "YouTube",
                      "Confidence",
                      "Source",
                    ].map((h) => (
                      <th
                        key={h}
                        className="px-4 py-4 text-xs font-bold uppercase tracking-wider text-slate-500"
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/8">
                  {rows.map((r) => (
                    <tr
                      key={`${r.name}-${r.facebook}-${r.instagram}`}
                      className="align-top transition-colors hover:bg-primary/[0.03]"
                    >
                      <td className="px-4 py-4 font-semibold text-slate-100">{r.name}</td>
                      <td className="px-4 py-4 text-sm text-slate-300">{r.category || "-"}</td>
                      <td className="px-4 py-4 text-sm text-slate-300">{r.subCategory || "-"}</td>
                      <td className="px-4 py-4"><LinkCell href={r.facebook} confidence={r.facebookConfidence} /></td>
                      <td className="px-4 py-4"><LinkCell href={r.instagram} confidence={r.instagramConfidence} /></td>
                      <td className="px-4 py-4"><LinkCell href={r.x} confidence={r.xConfidence} /></td>
                      <td className="px-4 py-4"><LinkCell href={r.tiktok} confidence={r.tiktokConfidence} /></td>
                      <td className="px-4 py-4"><LinkCell href={r.youtube} confidence={r.youtubeConfidence} /></td>
                      <td className="px-4 py-4"><ConfBadge value={r.confidence} /></td>
                      <td className="px-4 py-4 text-xs text-slate-400">{r.source || "-"}</td>
                    </tr>
                  ))}
                  {rows.length === 0 && (
                    <tr>
                      <td
                        colSpan={10}
                        className="px-6 py-10 text-center text-sm text-slate-500"
                      >
                        No output rows found yet. Run the Python pipeline first to
                        generate a `Talent_Social_Lookup_*.xlsx` file in
                        `C:\\Testing`, or ensure `NEXT_PUBLIC_PYTHON_API_URL` points at your
                        running FastAPI server.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
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
