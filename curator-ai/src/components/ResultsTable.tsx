"use client";

import { useMemo, useState } from "react";
import {
  RESULT_PLATFORMS,
  statusTone,
  STATUS_MANUAL,
  STATUS_NOT_FOUND,
  STATUS_STOPPED,
  STATUS_VERIFIED,
  STATUS_WRONG,
} from "@/lib/results-mapper";
import type { PlatformKey, PlatformResult, ResultRow } from "@/types/results";

/** Status filter options, in the order an analyst cares about them. */
const STATUS_FILTERS = [
  { value: STATUS_MANUAL, label: "Manual Review", icon: "help" },
  { value: STATUS_VERIFIED, label: "Verified", icon: "verified" },
  { value: STATUS_WRONG, label: "Wrong", icon: "cancel" },
  { value: STATUS_NOT_FOUND, label: "Not Found", icon: "search_off" },
  { value: STATUS_STOPPED, label: "Not Checked", icon: "pause_circle" },
] as const;

type PlatformScope = "any" | PlatformKey;

function ConfBadge({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  let cls = "bg-rose-500/10 text-rose-400 ring-rose-500/20";
  if (value > 0.8) cls = "bg-emerald-500/10 text-emerald-400 ring-emerald-500/20";
  else if (value >= 0.5) cls = "bg-amber-500/10 text-amber-400 ring-amber-500/20";
  return (
    <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-bold ring-1 ${cls}`}>
      {pct}%
    </span>
  );
}

function PlatformCell({ result, dimmed }: { result: PlatformResult; dimmed: boolean }) {
  const pct = Math.round(result.confidence * 100);
  const tone = statusTone(result.status);
  return (
    <div className={`min-w-[160px] space-y-1.5 transition-opacity ${dimmed ? "opacity-25" : ""}`}>
      <div className="flex items-center gap-2">
        <span
          className={`inline-flex rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ring-1 ${tone}`}
        >
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

export function ResultsTable({ rows }: { rows: ResultRow[] }) {
  const [statuses, setStatuses] = useState<Set<string>>(new Set());
  const [scope, setScope] = useState<PlatformScope>("any");
  const [query, setQuery] = useState("");
  const [triage, setTriage] = useState(false);

  /** Cell counts per status, so the chips show how much work each represents. */
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const r of rows) {
      for (const p of RESULT_PLATFORMS) {
        const s = r.platforms[p.key].status;
        if (s) c[s] = (c[s] ?? 0) + 1;
      }
    }
    return c;
  }, [rows]);

  const platformsInScope = useMemo(
    () => (scope === "any" ? RESULT_PLATFORMS : RESULT_PLATFORMS.filter((p) => p.key === scope)),
    [scope],
  );

  /** A cell matches when no status filter is active, or its status is selected. */
  const cellMatches = (r: ResultRow, key: PlatformKey) =>
    statuses.size === 0 ? true : statuses.has(r.platforms[key].status);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = rows.filter((r) => {
      if (q && !r.name.toLowerCase().includes(q)) return false;
      if (statuses.size === 0) return true;
      // Keep the row when any platform IN SCOPE carries a selected status.
      return platformsInScope.some((p) => statuses.has(r.platforms[p.key].status));
    });
    if (!triage) return filtered;
    // Triage order: most cells needing a human decision first.
    const needsWork = (r: ResultRow) =>
      RESULT_PLATFORMS.filter((p) => r.platforms[p.key].status === STATUS_MANUAL).length;
    return [...filtered].sort((a, b) => needsWork(b) - needsWork(a));
  }, [rows, query, statuses, platformsInScope, triage]);

  /** Cells actually shown, for the summary line under the filters. */
  const shownCells = useMemo(() => {
    let n = 0;
    for (const r of visible) {
      for (const p of platformsInScope) if (cellMatches(r, p.key)) n += 1;
    }
    return n;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, platformsInScope, statuses]);

  function toggleStatus(value: string) {
    setStatuses((prev) => {
      const next = new Set(prev);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return next;
    });
  }

  function reset() {
    setStatuses(new Set());
    setScope("any");
    setQuery("");
    setTriage(false);
  }

  const filtersActive = statuses.size > 0 || scope !== "any" || query.trim() !== "" || triage;

  return (
    <div className="space-y-4">
      {/* ── Filter bar ─────────────────────────────────────────────── */}
      <div className="lf-card space-y-4 p-4 sm:p-5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="mr-1 text-xs font-bold uppercase tracking-wider text-slate-500">
            Status
          </span>
          {STATUS_FILTERS.map((f) => {
            const on = statuses.has(f.value);
            const n = counts[f.value] ?? 0;
            return (
              <button
                key={f.value}
                type="button"
                aria-pressed={on}
                disabled={n === 0}
                onClick={() => toggleStatus(f.value)}
                className={`inline-flex cursor-pointer items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-bold ring-1 transition disabled:cursor-not-allowed disabled:opacity-35 ${
                  on
                    ? "bg-primary/20 text-primary ring-primary/40"
                    : "bg-slate-950/60 text-slate-400 ring-white/10 hover:text-slate-200"
                }`}
              >
                <span className="material-symbols-outlined text-sm">{f.icon}</span>
                {f.label}
                <span
                  className={`rounded-full px-1.5 py-0.5 text-[10px] tabular-nums ${
                    on ? "bg-primary/25" : "bg-white/5"
                  }`}
                >
                  {n}
                </span>
              </button>
            );
          })}
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <label className="inline-flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-slate-500">
            Platform
            <select
              value={scope}
              onChange={(e) => setScope(e.target.value as PlatformScope)}
              className="cursor-pointer rounded-lg border border-white/10 bg-slate-950/80 px-3 py-1.5 text-xs font-semibold text-slate-200 outline-none focus:border-primary/40"
            >
              <option value="any">Any platform</option>
              {RESULT_PLATFORMS.map((p) => (
                <option key={p.key} value={p.key}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>

          <div className="relative min-w-[200px] flex-1">
            <span className="material-symbols-outlined pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-base text-slate-500">
              search
            </span>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search talent name…"
              aria-label="Search talent name"
              className="w-full rounded-lg border border-white/10 bg-slate-950/80 py-1.5 pl-9 pr-3 text-xs font-semibold text-slate-200 placeholder:text-slate-600 outline-none focus:border-primary/40"
            />
          </div>

          <button
            type="button"
            aria-pressed={triage}
            onClick={() => setTriage((v) => !v)}
            title="Sort rows with the most Manual Review cells to the top"
            className={`inline-flex cursor-pointer items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-bold ring-1 transition ${
              triage
                ? "bg-amber-500/20 text-amber-200 ring-amber-500/40"
                : "bg-slate-950/60 text-slate-400 ring-white/10 hover:text-slate-200"
            }`}
          >
            <span className="material-symbols-outlined text-sm">low_priority</span>
            Triage order
          </button>

          {filtersActive && (
            <button
              type="button"
              onClick={reset}
              className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-bold text-slate-400 ring-1 ring-white/10 transition hover:text-slate-200"
            >
              <span className="material-symbols-outlined text-sm">filter_alt_off</span>
              Clear
            </button>
          )}
        </div>

        <p className="text-xs text-slate-500">
          Showing <strong className="text-slate-300">{visible.length}</strong> of {rows.length} row
          {rows.length === 1 ? "" : "s"}
          {statuses.size > 0 && (
            <>
              {" "}
              · <strong className="text-slate-300">{shownCells}</strong> matching cell
              {shownCells === 1 ? "" : "s"} highlighted
            </>
          )}
        </p>
      </div>

      {/* ── Table ──────────────────────────────────────────────────── */}
      <div className="lf-card overflow-hidden">
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
              {visible.map((r, i) => (
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
                  {RESULT_PLATFORMS.map((p) => {
                    const inScope = scope === "any" || scope === p.key;
                    return (
                      <td key={p.key} className="px-4 py-4">
                        <PlatformCell
                          result={r.platforms[p.key]}
                          dimmed={statuses.size > 0 && !(inScope && cellMatches(r, p.key))}
                        />
                      </td>
                    );
                  })}
                  <td className="px-4 py-4">
                    <ConfBadge value={r.confidence} />
                  </td>
                </tr>
              ))}
              {visible.length === 0 && (
                <tr>
                  <td
                    colSpan={RESULT_PLATFORMS.length + 3}
                    className="px-6 py-10 text-center text-sm text-slate-500"
                  >
                    {rows.length === 0
                      ? "No output rows found yet. Run the pipeline first to generate a `Talent_Social_Lookup_*.xlsx` file, or ensure `NEXT_PUBLIC_PYTHON_API_URL` points at your running FastAPI server."
                      : "No rows match these filters."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
