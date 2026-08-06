"use client";

import { memo, useCallback, useDeferredValue, useEffect, useMemo, useState } from "react";
import { useToast } from "@/components/ToastProvider";
import {
  checkDbHealth,
  lookupDecisions,
  recordDecision,
  type DbHealth,
  type DecisionKind,
  type DecisionMap,
} from "@/lib/decisions";
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

const STATUS_FILTERS = [
  { value: STATUS_MANUAL, label: "Manual Review", icon: "help" },
  { value: STATUS_VERIFIED, label: "Verified", icon: "verified" },
  { value: STATUS_WRONG, label: "Wrong", icon: "cancel" },
  { value: STATUS_NOT_FOUND, label: "Not Found", icon: "search_off" },
  { value: STATUS_STOPPED, label: "Not Checked", icon: "pause_circle" },
] as const;

type PlatformScope = "any" | PlatformKey;
/** Local decision state, keyed `lowercase title|Platform`. */
type DecisionState = Record<string, DecisionKind>;
type PendingState = Record<string, boolean>;

/** Rows rendered at once. 234 rows x 5 platforms is ~1,200 cells — enough DOM to
 *  make filtering and scrolling visibly stutter. Analysts work a screen at a
 *  time, so a page is both faster and easier to work through. */
const PAGE_SIZE = 50;

const cellKey = (title: string, platform: string) => `${title.toLowerCase()}|${platform}`;

/* ── small presentational pieces ─────────────────────────────────────────── */

function ConfBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const tone = value > 0.8 ? "var(--good)" : value >= 0.5 ? "var(--warn)" : "var(--bad)";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-12 overflow-hidden rounded-full bg-white/10">
        <div className="h-full rounded-full" style={{ width: `${pct}%`, background: tone }} />
      </div>
      <span className="text-xs font-bold tabular-nums" style={{ color: tone }}>{pct}%</span>
    </div>
  );
}

const PlatformCell = memo(function PlatformCell({
  rowName, result, platform, dimmed, decision, pending, onDecide,
}: {
  rowName: string;
  result: PlatformResult;
  platform: string;
  dimmed: boolean;
  decision?: DecisionKind;
  pending: boolean;
  onDecide: (rowName: string, platform: string, kind: DecisionKind) => void;
}) {
  const pct = Math.round(result.confidence * 100);
  const saved = decision === "verified";
  const rejected = decision === "rejected";

  // A decision the analyst has made outranks the pipeline's own label.
  const edge = saved ? "var(--good)" : rejected ? "var(--bad)" : "transparent";

  return (
    <div
      className={`group/cell relative min-w-[210px] rounded-lg border border-white/8 bg-slate-950/40 p-3 transition-all duration-200 ${
        dimmed ? "opacity-25" : "hover:border-white/16 hover:bg-slate-950/70"
      }`}
      style={{ boxShadow: `inset 3px 0 0 0 ${edge}` }}
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <span
          className={`inline-flex rounded px-1.5 py-0.5 text-[9.5px] font-bold uppercase tracking-wider ring-1 ${statusTone(result.status)}`}
        >
          {result.status || "—"}
        </span>
        {result.link && result.confidence > 0 && (
          <span className="text-[10px] font-bold tabular-nums text-slate-500">{pct}%</span>
        )}
      </div>

      {result.link ? (
        <a
          href={result.link}
          target="_blank"
          rel="noreferrer"
          className="block break-all text-xs leading-snug text-slate-300 underline-offset-2 transition hover:text-primary hover:underline"
        >
          {result.link.replace(/^https?:\/\/(www\.)?/, "")}
        </a>
      ) : (
        <span className="text-xs italic text-slate-600">No profile</span>
      )}

      {result.reason && (
        <p
          className="mt-2 line-clamp-2 text-[10.5px] leading-snug text-slate-500"
          title={result.reason}
        >
          {result.reason}
        </p>
      )}

      {result.link && (
        <div className="mt-2.5 flex items-center gap-1.5 border-t border-white/8 pt-2.5">
          <button
            type="button"
            disabled={pending}
            aria-pressed={saved}
            onClick={() => onDecide(rowName, platform, "verified")}
            title={`Save this ${platform} URL to verified_url`}
            className={`inline-flex flex-1 cursor-pointer items-center justify-center gap-1 rounded px-2 py-1.5 text-[10px] font-bold uppercase tracking-wide ring-1 transition disabled:cursor-wait disabled:opacity-50 ${
              saved
                ? "bg-emerald-500/25 text-emerald-200 ring-emerald-400/50"
                : "bg-white/[0.03] text-slate-400 ring-white/10 hover:bg-emerald-500/15 hover:text-emerald-200 hover:ring-emerald-400/40"
            }`}
          >
            <span className="material-symbols-outlined text-[13px]">
              {saved ? "task_alt" : "bookmark_add"}
            </span>
            {saved ? "Saved" : "Save"}
          </button>
          <button
            type="button"
            disabled={pending}
            aria-pressed={rejected}
            onClick={() => onDecide(rowName, platform, "rejected")}
            title={`Record this ${platform} URL in rejected_url`}
            className={`inline-flex flex-1 cursor-pointer items-center justify-center gap-1 rounded px-2 py-1.5 text-[10px] font-bold uppercase tracking-wide ring-1 transition disabled:cursor-wait disabled:opacity-50 ${
              rejected
                ? "bg-rose-500/25 text-rose-200 ring-rose-400/50"
                : "bg-white/[0.03] text-slate-400 ring-white/10 hover:bg-rose-500/15 hover:text-rose-200 hover:ring-rose-400/40"
            }`}
          >
            <span className="material-symbols-outlined text-[13px]">
              {rejected ? "block" : "thumb_down"}
            </span>
            {rejected ? "Rejected" : "Reject"}
          </button>
        </div>
      )}
    </div>
  );
});

/* ── main table ──────────────────────────────────────────────────────────── */

export function ResultsTable({ rows }: { rows: ResultRow[] }) {
  const { pushToast } = useToast();
  const [statuses, setStatuses] = useState<Set<string>>(new Set());
  const [scope, setScope] = useState<PlatformScope>("any");
  const [query, setQuery] = useState("");
  const [triage, setTriage] = useState(false);
  const [decisions, setDecisions] = useState<DecisionState>({});
  const [pending, setPending] = useState<PendingState>({});
  const [db, setDb] = useState<DbHealth | null>(null);
  const [page, setPage] = useState(0);

  /* Load DB health + any decisions already recorded for these titles. */
  useEffect(() => {
    let alive = true;
    void (async () => {
      const health = await checkDbHealth();
      if (!alive) return;
      setDb(health);
      if (!health?.connected || rows.length === 0) return;
      try {
        const { decisions: saved } = await lookupDecisions(rows.map((r) => r.name));
        if (!alive) return;
        setDecisions(flatten(saved));
      } catch {
        /* page still works without prior decisions */
      }
    })();
    return () => {
      alive = false;
    };
  }, [rows]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const r of rows)
      for (const p of RESULT_PLATFORMS) {
        const s = r.platforms[p.key].status;
        if (s) c[s] = (c[s] ?? 0) + 1;
      }
    return c;
  }, [rows]);

  const decidedCounts = useMemo(() => {
    let v = 0, x = 0;
    for (const k of Object.values(decisions)) {
      if (k === "verified") v += 1;
      else x += 1;
    }
    return { verified: v, rejected: x };
  }, [decisions]);

  const platformsInScope = useMemo(
    () => (scope === "any" ? RESULT_PLATFORMS : RESULT_PLATFORMS.filter((p) => p.key === scope)),
    [scope],
  );

  const cellMatches = useCallback(
    (r: ResultRow, key: PlatformKey) =>
      statuses.size === 0 ? true : statuses.has(r.platforms[key].status),
    [statuses],
  );

  const deferredQuery = useDeferredValue(query);
  const visible = useMemo(() => {
    const q = deferredQuery.trim().toLowerCase();
    const filtered = rows.filter((r) => {
      if (q && !r.name.toLowerCase().includes(q)) return false;
      if (statuses.size === 0) return true;
      return platformsInScope.some((p) => statuses.has(r.platforms[p.key].status));
    });
    if (!triage) return filtered;
    const needsWork = (r: ResultRow) =>
      RESULT_PLATFORMS.filter((p) => r.platforms[p.key].status === STATUS_MANUAL).length;
    return [...filtered].sort((a, b) => needsWork(b) - needsWork(a));
  }, [rows, deferredQuery, statuses, platformsInScope, triage]);

  const pageCount = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const pageRows = useMemo(
    () => visible.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE),
    [visible, safePage],
  );

  // Filters change the result set, so jump back to the first page — otherwise a
  // narrow filter can land on an empty page and look like "no results".
  useEffect(() => {
    setPage(0);
  }, [deferredQuery, statuses, scope, triage]);

  const decide = useCallback(async (rowName: string, platform: string, kind: DecisionKind) => {
    const row = rows.find((r) => r.name === rowName);
    if (!row) return;
    const result = row.platforms[
      RESULT_PLATFORMS.find((p) => p.label === platform)!.key
    ];
    if (!result.link) return;
    const key = cellKey(row.name, platform);
    setPending((p) => ({ ...p, [key]: true }));
    try {
      await recordDecision(kind, { title: row.name, platform, url: result.link });
      setDecisions((d) => ({ ...d, [key]: kind }));
      pushToast(
        `${platform} ${kind === "verified" ? "saved to verified_url" : "recorded in rejected_url"} — ${row.name}`,
        "success",
      );
    } catch (e) {
      pushToast(e instanceof Error ? e.message : "Could not save decision.", "error");
    } finally {
      setPending((p) => ({ ...p, [key]: false }));
    }
  }, [rows, pushToast]);

  const toggleStatus = (v: string) =>
    setStatuses((prev) => {
      const next = new Set(prev);
      if (next.has(v)) next.delete(v);
      else next.add(v);
      return next;
    });

  const reset = () => {
    setStatuses(new Set());
    setScope("any");
    setQuery("");
    setTriage(false);
  };

  const filtersActive = statuses.size > 0 || scope !== "any" || query.trim() !== "" || triage;

  return (
    <div className="space-y-4" style={{ ["--good" as string]: "#34d399", ["--warn" as string]: "#fbbf24", ["--bad" as string]: "#fb7185" }}>
      {/* ── database status ─────────────────────────────────────────────── */}
      {db && !db.connected && (
        <div className="flex items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-500/[0.07] px-4 py-3">
          <span className="material-symbols-outlined mt-0.5 text-base text-amber-400">database</span>
          <div className="min-w-0 text-sm">
            <p className="font-semibold text-amber-200">Save and Reject are unavailable</p>
            <p className="mt-0.5 text-xs text-amber-200/70">{db.detail}</p>
          </div>
        </div>
      )}

      {/* ── control bar ─────────────────────────────────────────────────── */}
      <div className="lf-card space-y-4 p-4 sm:p-5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="mr-1 text-[11px] font-bold uppercase tracking-widest text-slate-500">
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
                className={`inline-flex cursor-pointer items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-bold ring-1 transition disabled:cursor-not-allowed disabled:opacity-30 ${
                  on
                    ? "bg-primary/20 text-primary ring-primary/40"
                    : "bg-slate-950/60 text-slate-400 ring-white/10 hover:text-slate-200"
                }`}
              >
                <span className="material-symbols-outlined text-sm">{f.icon}</span>
                {f.label}
                <span className={`rounded-full px-1.5 text-[10px] tabular-nums ${on ? "bg-primary/25" : "bg-white/5"}`}>
                  {n}
                </span>
              </button>
            );
          })}
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <label className="inline-flex items-center gap-2 text-[11px] font-bold uppercase tracking-widest text-slate-500">
            Platform
            <select
              value={scope}
              onChange={(e) => setScope(e.target.value as PlatformScope)}
              className="cursor-pointer rounded-lg border border-white/10 bg-slate-950/80 px-3 py-1.5 text-xs font-semibold text-slate-200 outline-none focus:border-primary/40"
            >
              <option value="any">Any platform</option>
              {RESULT_PLATFORMS.map((p) => (
                <option key={p.key} value={p.key}>{p.label}</option>
              ))}
            </select>
          </label>

          <div className="relative min-w-[190px] flex-1">
            <span className="material-symbols-outlined pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-base text-slate-500">
              search
            </span>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search talent name…"
              aria-label="Search talent name"
              className="w-full rounded-lg border border-white/10 bg-slate-950/80 py-1.5 pl-9 pr-3 text-xs font-semibold text-slate-200 outline-none placeholder:text-slate-600 focus:border-primary/40"
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

        <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-slate-500">
          <span>
            Showing <strong className="text-slate-300">{pageRows.length}</strong> of {visible.length} matching row
            {rows.length === 1 ? "" : "s"}
          </span>
          {(decidedCounts.verified > 0 || decidedCounts.rejected > 0) && (
            <span className="inline-flex items-center gap-3">
              <span className="inline-flex items-center gap-1.5">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                <strong className="text-slate-300">{decidedCounts.verified}</strong> saved
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span className="h-1.5 w-1.5 rounded-full bg-rose-400" />
                <strong className="text-slate-300">{decidedCounts.rejected}</strong> rejected
              </span>
            </span>
          )}
        </div>
      </div>

      {/* ── results ─────────────────────────────────────────────────────── */}
      <div className="lf-card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1500px] border-collapse text-left">
            <thead>
              <tr className="border-b border-white/10 bg-slate-950/80">
                <th className="sticky left-0 z-10 bg-slate-950/95 px-4 py-3.5 text-[10.5px] font-bold uppercase tracking-widest text-slate-500 backdrop-blur">
                  Talent
                </th>
                {RESULT_PLATFORMS.map((p) => (
                  <th key={p.key} className="px-3 py-3.5 text-[10.5px] font-bold uppercase tracking-widest text-slate-500">
                    <span className="inline-flex items-center gap-1.5">
                      <span className="material-symbols-outlined text-sm text-slate-600">{p.icon}</span>
                      {p.label}
                    </span>
                  </th>
                ))}
                <th className="px-4 py-3.5 text-[10.5px] font-bold uppercase tracking-widest text-slate-500">
                  Overall
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.06]">
              {pageRows.map((r, i) => (
                <tr key={`${r.name}-${i}`} className="align-top transition-colors hover:bg-white/[0.015]">
                  <td className="sticky left-0 z-10 min-w-[190px] bg-slate-950/95 px-4 py-4 backdrop-blur">
                    <div className="font-semibold leading-snug text-slate-100">{r.name}</div>
                    {r.wikipediaUrl && (
                      <a
                        href={r.wikipediaUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-1 inline-flex items-center gap-1 text-[10.5px] text-slate-500 transition hover:text-primary"
                      >
                        <span className="material-symbols-outlined text-[13px]">menu_book</span>
                        Wikipedia
                      </a>
                    )}
                  </td>
                  {RESULT_PLATFORMS.map((p) => {
                    const inScope = scope === "any" || scope === p.key;
                    const key = cellKey(r.name, p.label);
                    return (
                      <td key={p.key} className="px-3 py-4">
                        <PlatformCell
                          rowName={r.name}
                          result={r.platforms[p.key]}
                          platform={p.label}
                          dimmed={statuses.size > 0 && !(inScope && cellMatches(r, p.key))}
                          decision={decisions[key]}
                          pending={!!pending[key]}
                          onDecide={decide}
                        />
                      </td>
                    );
                  })}
                  <td className="px-4 py-4"><ConfBar value={r.confidence} /></td>
                </tr>
              ))}
              {visible.length === 0 && (
                <tr>
                  <td colSpan={RESULT_PLATFORMS.length + 2} className="px-6 py-14 text-center">
                    <span className="material-symbols-outlined mb-2 block text-3xl text-slate-700">
                      {rows.length === 0 ? "inbox" : "filter_alt_off"}
                    </span>
                    <p className="text-sm text-slate-500">
                      {rows.length === 0
                        ? "No output yet. Run the pipeline to generate results."
                        : "No rows match these filters."}
                    </p>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {pageCount > 1 && (
          <div className="flex items-center justify-between gap-3 border-t border-white/10 px-4 py-3">
            <span className="text-xs text-slate-500">
              Rows{" "}
              <strong className="tabular-nums text-slate-300">
                {safePage * PAGE_SIZE + 1}–{safePage * PAGE_SIZE + pageRows.length}
              </strong>{" "}
              of <strong className="tabular-nums text-slate-300">{visible.length}</strong>
            </span>
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => setPage((n) => Math.max(0, n - 1))}
                disabled={safePage === 0}
                className="inline-flex cursor-pointer items-center gap-1 rounded-lg px-3 py-1.5 text-xs font-bold text-slate-300 ring-1 ring-white/10 transition hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-30"
              >
                <span className="material-symbols-outlined text-sm">chevron_left</span>
                Previous
              </button>
              <span className="px-2 text-xs font-bold tabular-nums text-slate-400">
                {safePage + 1} / {pageCount}
              </span>
              <button
                type="button"
                onClick={() => setPage((n) => Math.min(pageCount - 1, n + 1))}
                disabled={safePage >= pageCount - 1}
                className="inline-flex cursor-pointer items-center gap-1 rounded-lg px-3 py-1.5 text-xs font-bold text-slate-300 ring-1 ring-white/10 transition hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-30"
              >
                Next
                <span className="material-symbols-outlined text-sm">chevron_right</span>
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/** Server shape → flat `title|Platform` keys. Verified wins if both exist. */
function flatten(map: DecisionMap): DecisionState {
  const out: DecisionState = {};
  for (const [title, slots] of Object.entries(map)) {
    for (const [platform, url] of Object.entries(slots.rejected ?? {}))
      if (url) out[cellKey(title, platform)] = "rejected";
    for (const [platform, url] of Object.entries(slots.verified ?? {}))
      if (url) out[cellKey(title, platform)] = "verified";
  }
  return out;
}
