import type { Metadata } from "next";
import Link from "next/link";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import * as XLSX from "xlsx";
import { AppMobileNav } from "@/components/AppMobileNav";
import { AppSidebar } from "@/components/AppSidebar";

const REQUIRED_PREFIX = "Talent_Social_Lookup_";
const REQUIRED_SUFFIX = ".xlsx";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Analysis | ListenFirst",
  description: "Confidence distribution analysis for resolved social links.",
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

type RowShape = {
  facebook: string;
  facebookConfidence: number;
  instagram: string;
  instagramConfidence: number;
  x: string;
  xConfidence: number;
  tiktok: string;
  tiktokConfidence: number;
  youtube: string;
  youtubeConfidence: number;
};

function mapRecordToRow(r: Record<string, unknown>): RowShape {
  return {
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
  };
}

function readRowsFromWorkbook(wb: XLSX.WorkBook): RowShape[] {
  const firstSheetName = wb.SheetNames[0];
  if (!firstSheetName) return [];
  const sheet = wb.Sheets[firstSheetName];
  const jsonRows = XLSX.utils.sheet_to_json<Record<string, unknown>>(sheet, { defval: "" });
  return jsonRows.map((r) => mapRecordToRow(r));
}

async function readRowsFromPathWithRetry(fullPath: string): Promise<RowShape[]> {
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

async function loadRows(): Promise<RowShape[]> {
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
  const platformLinks = rows.flatMap((r) => [
    { link: r.facebook, conf: r.facebookConfidence },
    { link: r.instagram, conf: r.instagramConfidence },
    { link: r.x, conf: r.xConfidence },
    { link: r.tiktok, conf: r.tiktokConfidence },
    { link: r.youtube, conf: r.youtubeConfidence },
  ]);
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
    <div className="flex min-h-screen flex-col bg-background md:flex-row">
      <AppSidebar />

      <main className="md:ml-64 flex-1 p-4 pb-24 md:p-8 md:pb-8">
        <header className="sticky top-0 z-30 mb-6 flex items-center justify-between bg-background/90 py-3 backdrop-blur-md">
          <h1 className="text-lg font-bold text-slate-100">Analysis</h1>
          <Link
            href="/results"
            className="inline-flex items-center gap-2 rounded-xl bg-slate-800 px-4 py-2 text-sm font-semibold text-slate-100 ring-1 ring-white/10 transition hover:bg-slate-700"
          >
            <span className="material-symbols-outlined text-base">arrow_back</span>
            Close Analysis
          </Link>
        </header>

        <div className="mx-auto max-w-6xl space-y-8">
          <section className="grid grid-cols-1 gap-8 lg:grid-cols-2">
            <div className="flex min-h-[500px] items-center justify-center rounded-2xl border border-white/10 bg-slate-900/75 p-6 ring-1 ring-white/10">
              <div className="relative h-[360px] w-[360px] rounded-full p-5" style={chartStyle}>
                <div className="absolute inset-[18%] flex items-center justify-center rounded-full bg-slate-900 ring-1 ring-white/10">
                  <div className="text-center">
                    <div className="text-xs font-semibold uppercase tracking-widest text-slate-400">
                      Total Links
                    </div>
                    <div className="mt-1 text-4xl font-black text-slate-100">{total}</div>
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
              />
              <AnalysisRow
                label="Yellow (70%-85%)"
                count={yellowCount}
                pct={asPct(yellowCount)}
                tone="amber"
              />
              <AnalysisRow label="Red (<70%)" count={redCount} pct={asPct(redCount)} tone="rose" />
              <div className="mt-4 rounded-xl border border-indigo-500/25 bg-indigo-500/10 px-4 py-3 text-sm text-indigo-100">
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
}: {
  label: string;
  count: number;
  pct: string;
  tone: "emerald" | "amber" | "rose";
}) {
  const cls =
    tone === "emerald"
      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200"
      : tone === "amber"
        ? "border-amber-500/30 bg-amber-500/10 text-amber-200"
        : "border-rose-500/30 bg-rose-500/10 text-rose-200";
  return (
    <div className={`flex items-center justify-between rounded-xl border px-4 py-3 ${cls}`}>
      <span className="text-sm font-semibold">{label}</span>
      <span className="text-sm font-bold">
        {count} <span className="opacity-80">({pct})</span>
      </span>
    </div>
  );
}

