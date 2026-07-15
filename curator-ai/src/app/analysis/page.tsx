import type { Metadata } from "next";
import Link from "next/link";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import * as XLSX from "xlsx";
import { AppMobileNav } from "@/components/AppMobileNav";
import { AppPageHeader } from "@/components/AppPageHeader";
import { AppSidebar } from "@/components/AppSidebar";
import { mapRecordToRow, RESULT_PLATFORMS } from "@/lib/results-mapper";
import type { ResultRow } from "@/types/results";

const REQUIRED_PREFIX = "Talent_Social_Lookup_";
const REQUIRED_SUFFIX = ".xlsx";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Analysis | ListenFirst",
  description: "Confidence distribution analysis for verified social links.",
};

function getPythonApiUrl(): string | null {
  const u = process.env.NEXT_PUBLIC_PYTHON_API_URL?.trim().replace(/\/$/, "");
  return u && u.length > 0 ? u : null;
}

function readRowsFromWorkbook(wb: XLSX.WorkBook): ResultRow[] {
  const firstSheetName = wb.SheetNames[0];
  if (!firstSheetName) return [];
  const sheet = wb.Sheets[firstSheetName];
  const jsonRows = XLSX.utils.sheet_to_json<Record<string, unknown>>(sheet, { defval: "" });
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

async function loadRows(): Promise<ResultRow[]> {
  const api = getPythonApiUrl();
  if (api) {
    try {
      const res = await fetch(`${api}/api/results/latest`, { cache: "no-store" });
      if (res.ok) {
        const data = (await res.json()) as { rows?: Record<string, unknown>[] };
        if (data.rows && data.rows.length > 0) {
          return data.rows.map((r) => mapRecordToRow(r));
        }
      }
    } catch {
      // fall back to local file read
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
  if (candidates.length === 0) return [];
  const fullPath = path.join(dataDir, candidates[0]);
  return readRowsFromPathWithRetry(fullPath);
}

export default async function AnalysisPage() {
  const rows = await loadRows();
  const platformLinks = rows.flatMap((r) =>
    RESULT_PLATFORMS.map((p) => ({
      link: r.platforms[p.key].link,
      conf: r.platforms[p.key].confidence,
    })),
  );
  const resolvedLinks = platformLinks.filter((x) => x.link && x.link.trim().length > 0);
  const greenCount = resolvedLinks.filter((x) => x.conf * 100 > 85).length;
  const yellowCount = resolvedLinks.filter((x) => x.conf * 100 >= 70 && x.conf * 100 <= 85).length;
  const redCount = resolvedLinks.filter((x) => x.conf * 100 < 70).length;
  const total = greenCount + yellowCount + redCount;

  const greenDeg = total ? (greenCount / total) * 360 : 0;
  const yellowDeg = total ? (yellowCount / total) * 360 : 0;
  const chartStyle = {
    background:
      total > 0
        ? `conic-gradient(
          rgba(16,185,129,0.95) 0deg ${greenDeg}deg,
          rgba(245,158,11,0.95) ${greenDeg}deg ${greenDeg + yellowDeg}deg,
          rgba(244,63,94,0.95) ${greenDeg + yellowDeg}deg 360deg
        )`
        : "conic-gradient(rgba(71,85,105,0.5) 0deg 360deg)",
  };

  const asPct = (v: number) => (total ? `${((v / total) * 100).toFixed(1)}%` : "0.0%");

  return (
    <div className="relative flex min-h-screen flex-col bg-background md:flex-row">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_0%_0%,rgba(242,209,0,0.06),transparent_32%)]"
      />
      <AppSidebar />

      <main className="relative z-10 flex-1 p-4 pb-24 md:ml-64 md:p-8 md:pb-8">
        <AppPageHeader
          title="Analysis"
          subtitle="Confidence breakdown"
          icon="donut_large"
          actions={
            <Link href="/results" className="lf-btn-secondary inline-flex items-center gap-2 px-4 py-2.5 text-sm">
              <span className="material-symbols-outlined text-base">arrow_back</span>
              Back to Results
            </Link>
          }
        />

        <div className="mx-auto max-w-6xl space-y-8">
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
                        Total Links
                      </div>
                      <div className="mt-1 text-4xl font-black text-slate-100">{total}</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div className="space-y-4">
              <AnalysisRow
                label="Green (>85%)"
                count={greenCount}
                pct={asPct(greenCount)}
                tone="emerald"
                icon="check_circle"
              />
              <AnalysisRow
                label="Yellow (70%-85%)"
                count={yellowCount}
                pct={asPct(yellowCount)}
                tone="amber"
                icon="warning"
              />
              <AnalysisRow
                label="Red (<70%)"
                count={redCount}
                pct={asPct(redCount)}
                tone="rose"
                icon="cancel"
              />
              <div className="lf-enter lf-enter-delay-2 lf-card mt-4 flex items-start gap-3 px-4 py-4 text-sm text-slate-300">
                <span className="material-symbols-outlined text-primary">insights</span>
                This chart includes all resolved platform links from the latest processed workbook.
              </div>
            </div>
          </section>
        </div>
      </main>

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
  tone: "emerald" | "amber" | "rose";
  icon: string;
}) {
  const cls =
    tone === "emerald"
      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200 lf-stat-glow-emerald"
      : tone === "amber"
        ? "border-amber-500/30 bg-amber-500/10 text-amber-200 lf-stat-glow-amber"
        : "border-rose-500/30 bg-rose-500/10 text-rose-200";

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
