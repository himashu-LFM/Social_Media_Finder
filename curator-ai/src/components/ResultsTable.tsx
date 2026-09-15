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
  confidenceToneClass,
  RESULT_PLATFORMS,
  statusIcon,
  statusToneClass,
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
/** Which cell the review panel is showing. */
type Selection = { name: string; platform: PlatformKey } | null;

/** Rows rendered at once. 234 rows x 5 platforms is ~1,200 cells — enough DOM to
 *  make filtering and scrolling visibly stutter. Analysts work a screen at a
 *  time, so a page is both faster and easier to work through. */
const PAGE_SIZE = 50;

const cellKey = (title: string, platform: string) => `${title.toLowerCase()}|${platform}`;

/**
 * The part of a profile URL worth showing in a cell: the handle.
 *
 * The handoff's mock data uses short URLs, so it prints them whole. Real ones
 * ("facebook.com/marcusfeldmanmusic") are long enough that the domain pushes
 * the identifying half out of view — and the domain is already given by the
 * column header and the platform icon. The full URL stays in the title
 * attribute and in the review panel.
 */
function handleOf(url: string): string {
  const bare = url.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "");
  const slash = bare.indexOf("/");
  const path = slash === -1 ? "" : bare.slice(slash + 1);
  return path || bare;
}

/* ── the compact cell ────────────────────────────────────────────────────── */

/**
 * One talent × platform cell.
 *
 * The handoff replaced the old expanded card — status pill, full URL, reason,
 * two buttons, all in every cell — with a single line: an icon in the status
 * colour, the handle, and the confidence. Five of those fit on a screen where
 * one card used to, and the detail moves to the review panel on the right.
 */
const PlatformCell = memo(function PlatformCell({
  rowName,
  result,
  platformKey,
  platformLabel,
  dimmed,
  selected,
  decision,
  onSelect,
}: {
  rowName: string;
  result: PlatformResult;
  platformKey: PlatformKey;
  platformLabel: string;
  dimmed: boolean;
  selected: boolean;
  decision?: DecisionKind;
  onSelect: (name: string, platform: PlatformKey) => void;
}) {
  const pct = Math.round(result.confidence * 100);
  const handle = result.link ? handleOf(result.link) : "No profile";

  return (
    <button
      type="button"
      aria-pressed={selected}
      aria-label={`${platformLabel} for ${rowName}: ${result.status || "no result"}`}
      onClick={() => onSelect(rowName, platformKey)}
      className={`sc-cellbtn ${statusToneClass(result.status)}${dimmed ? " dimmed" : ""}`}
    >
      <span className="material-symbols-outlined">{statusIcon(result.status)}</span>
      <span className="sc-cell-text" title={result.link || result.status}>
        {handle}
      </span>
      {decision && (
        <span
          className={`material-symbols-outlined sc-cell-mark ${decision === "verified" ? "saved" : "rejected"}`}
          title={decision === "verified" ? "Saved" : "Rejected"}
        >
          {decision === "verified" ? "task_alt" : "block"}
        </span>
      )}
      {result.link && result.confidence > 0 && <span className="sc-cell-pct">{pct}%</span>}
    </button>
  );
});

/* ── main table ──────────────────────────────────────────────────────────── */

export function ResultsTable({ rows, notice }: { rows: ResultRow[]; notice?: React.ReactNode }) {
  const { pushToast } = useToast();
  const [statuses, setStatuses] = useState<Set<string>>(new Set());
  const [scope, setScope] = useState<PlatformScope>("any");
  const [query, setQuery] = useState("");
  const [triage, setTriage] = useState(false);
  const [decisions, setDecisions] = useState<DecisionState>({});
  const [pending, setPending] = useState<PendingState>({});
  const [db, setDb] = useState<DbHealth | null>(null);
  const [page, setPage] = useState(0);
  const [selection, setSelection] = useState<Selection>(null);

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
    let v = 0,
      x = 0;
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

  // Open the first cell that needs a human as soon as rows arrive, so the panel
  // is never an empty box and the review queue starts where the work is.
  useEffect(() => {
    if (selection || rows.length === 0) return;
    for (const r of rows)
      for (const p of RESULT_PLATFORMS)
        if (r.platforms[p.key].status === STATUS_MANUAL) {
          setSelection({ name: r.name, platform: p.key });
          return;
        }
    setSelection({ name: rows[0].name, platform: RESULT_PLATFORMS[0].key });
  }, [rows, selection]);

  const selectCell = useCallback(
    (name: string, platform: PlatformKey) => setSelection({ name, platform }),
    [],
  );

  const decide = useCallback(
    async (rowName: string, platform: string, kind: DecisionKind) => {
      const row = rows.find((r) => r.name === rowName);
      if (!row) return;
      const result = row.platforms[RESULT_PLATFORMS.find((p) => p.label === platform)!.key];
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
    },
    [rows, pushToast],
  );

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

  /* ── the selected cell, for the review panel ───────────────────────────── */
  const selectedRow = selection ? rows.find((r) => r.name === selection.name) : undefined;
  const selectedMeta = selection
    ? RESULT_PLATFORMS.find((p) => p.key === selection.platform)
    : undefined;
  const selected = selectedRow && selection ? selectedRow.platforms[selection.platform] : undefined;
  const selectedKey =
    selectedRow && selectedMeta ? cellKey(selectedRow.name, selectedMeta.label) : "";
  const selectedDecision = decisions[selectedKey];
  const selectedPending = !!pending[selectedKey];

  const decidedTotal = decidedCounts.verified + decidedCounts.rejected;

  // The queue is the pipeline's own Manual Review cells — a fixed pool that a
  // decision moves through, not one that grows as decisions are made. Counting
  // every decision against it would inflate the denominator each time an
  // analyst resolved a cell, so only decisions on manual cells count here.
  const manualTotal = counts[STATUS_MANUAL] ?? 0;
  const manualDecided = useMemo(() => {
    let n = 0;
    for (const r of rows)
      for (const p of RESULT_PLATFORMS)
        if (
          r.platforms[p.key].status === STATUS_MANUAL &&
          decisions[cellKey(r.name, p.label)]
        )
          n += 1;
    return n;
  }, [rows, decisions]);
  const manualRemaining = Math.max(0, manualTotal - manualDecided);

  return (
    <div className="sc-page">
      <div className="sc-col">
        {notice}

        {db && !db.connected && (
          <div className="sc-banner sc-tone-warn">
            <span className="material-symbols-outlined">database</span>
            <span>
              <strong>Save and Reject are unavailable</strong> — {db.detail}
            </span>
          </div>
        )}

        {/* ── filters ───────────────────────────────────────────────────── */}
        <section
          className="sc-card"
          style={{ display: "flex", flexDirection: "column", gap: 10, padding: "12px 14px" }}
        >
          <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 6 }}>
            <span
              style={{
                marginRight: 4,
                fontSize: 10,
                fontWeight: 800,
                letterSpacing: "0.12em",
                textTransform: "uppercase",
                color: "#64748b",
                whiteSpace: "nowrap",
              }}
            >
              Status
            </span>
            {STATUS_FILTERS.map((f) => {
              const on = statuses.has(f.value);
              const n = counts[f.value] ?? 0;
              return (
                <button
                  key={f.value}
                  type="button"
                  className="sc-fchip"
                  aria-pressed={on}
                  disabled={n === 0}
                  onClick={() => toggleStatus(f.value)}
                >
                  <span className="material-symbols-outlined">{f.icon}</span>
                  {f.label}
                  <span className="sc-fchip-n">{n}</span>
                </button>
              );
            })}
          </div>

          <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
            <label
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 7,
                fontSize: 10,
                fontWeight: 800,
                letterSpacing: "0.12em",
                textTransform: "uppercase",
                color: "#64748b",
                whiteSpace: "nowrap",
              }}
            >
              Platform
              <select
                className="sc-select"
                value={scope}
                onChange={(e) => setScope(e.target.value as PlatformScope)}
              >
                <option value="any">Any platform</option>
                {RESULT_PLATFORMS.map((p) => (
                  <option key={p.key} value={p.key}>
                    {p.label}
                  </option>
                ))}
              </select>
            </label>

            <span className="sc-search fill">
              <span className="material-symbols-outlined">search</span>
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search talent name…"
                aria-label="Search talent name"
              />
            </span>

            <button
              type="button"
              className="sc-btn"
              aria-pressed={triage}
              onClick={() => setTriage((v) => !v)}
              title="Sort rows with the most Manual Review cells to the top"
            >
              <span className="material-symbols-outlined" style={{ fontSize: 15 }}>
                low_priority
              </span>
              Triage order
            </button>

            {filtersActive && (
              <button type="button" className="sc-btn ghost" onClick={reset}>
                <span className="material-symbols-outlined" style={{ fontSize: 15 }}>
                  filter_alt_off
                </span>
                Clear
              </button>
            )}
          </div>

          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              alignItems: "center",
              gap: "6px 18px",
              fontSize: 11,
              color: "#64748b",
            }}
          >
            <span style={{ whiteSpace: "nowrap" }}>
              Showing <strong style={{ color: "#cbd5e1" }}>{pageRows.length}</strong> of{" "}
              {visible.length} matching row{visible.length === 1 ? "" : "s"}
            </span>
            {decidedTotal > 0 && (
              <>
                <span className="sc-foot-item sc-tone-good">
                  <span className="sc-dot" />
                  <strong style={{ color: "#cbd5e1" }}>{decidedCounts.verified}</strong> saved
                </span>
                <span className="sc-foot-item sc-tone-bad">
                  <span className="sc-dot" />
                  <strong style={{ color: "#cbd5e1" }}>{decidedCounts.rejected}</strong> rejected
                </span>
              </>
            )}
          </div>
        </section>

        {/* ── the grid ──────────────────────────────────────────────────── */}
        <section className="sc-card" style={{ overflow: "hidden" }}>
          <div className="sc-data-wrap">
            <table className="sc-data cols" style={{ minWidth: 1080 }}>
              <thead>
                <tr>
                  <th style={{ width: 170 }}>Talent</th>
                  {RESULT_PLATFORMS.map((p) => (
                    <th key={p.key}>
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                        <span
                          className="material-symbols-outlined"
                          style={{ fontSize: 14, color: "#475569" }}
                        >
                          {p.icon}
                        </span>
                        {p.label}
                      </span>
                    </th>
                  ))}
                  <th style={{ width: 110 }}>Overall</th>
                </tr>
              </thead>
              <tbody>
                {pageRows.map((r, i) => (
                  <tr
                    key={`${r.name}-${i}`}
                    className={selection?.name === r.name ? "sc-row-active" : ""}
                  >
                    <td style={{ padding: "7px 14px" }}>
                      <span
                        style={{
                          display: "block",
                          fontSize: 12.5,
                          fontWeight: 700,
                          color: "#f1f5f9",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {r.name}
                      </span>
                      {r.wikipediaUrl && (
                        <a
                          href={r.wikipediaUrl}
                          target="_blank"
                          rel="noreferrer"
                          style={{
                            display: "inline-flex",
                            alignItems: "center",
                            gap: 4,
                            marginTop: 2,
                            fontSize: 10,
                            color: "#64748b",
                          }}
                        >
                          <span className="material-symbols-outlined" style={{ fontSize: 12 }}>
                            menu_book
                          </span>
                          Wikipedia
                        </a>
                      )}
                    </td>
                    {RESULT_PLATFORMS.map((p) => {
                      const inScope = scope === "any" || scope === p.key;
                      return (
                        <td key={p.key} style={{ padding: "5px 6px" }}>
                          <PlatformCell
                            rowName={r.name}
                            result={r.platforms[p.key]}
                            platformKey={p.key}
                            platformLabel={p.label}
                            dimmed={statuses.size > 0 && !(inScope && cellMatches(r, p.key))}
                            selected={selection?.name === r.name && selection.platform === p.key}
                            decision={decisions[cellKey(r.name, p.label)]}
                            onSelect={selectCell}
                          />
                        </td>
                      );
                    })}
                    <td
                      className={confidenceToneClass(r.confidence)}
                      style={{ padding: "7px 14px" }}
                    >
                      <span className="sc-conf" style={{ justifyContent: "flex-start" }}>
                        <span className="sc-conf-track wide">
                          <span
                            className="sc-conf-fill"
                            style={{ width: `${Math.round(r.confidence * 100)}%` }}
                          />
                        </span>
                        <span className="sc-conf-pct">{Math.round(r.confidence * 100)}%</span>
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {visible.length === 0 && (
              <div className="sc-empty">
                <span className="material-symbols-outlined">
                  {rows.length === 0 ? "inbox" : "filter_alt_off"}
                </span>
                {rows.length === 0
                  ? "No output yet. Run the pipeline to generate results."
                  : "No rows match these filters."}
              </div>
            )}
          </div>

          {visible.length > 0 && (
            <div className="sc-foot">
              <span>
                Rows{" "}
                <strong style={{ fontVariantNumeric: "tabular-nums" }}>
                  {safePage * PAGE_SIZE + 1}–{safePage * PAGE_SIZE + pageRows.length}
                </strong>{" "}
                of <strong style={{ fontVariantNumeric: "tabular-nums" }}>{visible.length}</strong>
              </span>
              {pageCount > 1 && (
                <span className="sc-pager">
                  <button
                    type="button"
                    className="sc-pager-btn"
                    onClick={() => setPage((n) => Math.max(0, n - 1))}
                    disabled={safePage === 0}
                  >
                    <span className="material-symbols-outlined">chevron_left</span>
                    Previous
                  </button>
                  <span className="sc-pager-at">
                    {safePage + 1} / {pageCount}
                  </span>
                  <button
                    type="button"
                    className="sc-pager-btn"
                    onClick={() => setPage((n) => Math.min(pageCount - 1, n + 1))}
                    disabled={safePage >= pageCount - 1}
                  >
                    Next
                    <span className="material-symbols-outlined">chevron_right</span>
                  </button>
                </span>
              )}
            </div>
          )}
        </section>

        <div className="sc-foot" style={{ border: 0, padding: "0 2px" }}>
          {STATUS_FILTERS.map((f) => (
            <span key={f.value} className={`sc-foot-item ${statusToneClass(f.value)}`}>
              <span className="material-symbols-outlined" style={{ color: "var(--tone)" }}>
                {statusIcon(f.value)}
              </span>
              {f.value}
            </span>
          ))}
          <span style={{ marginLeft: "auto", whiteSpace: "nowrap" }}>
            Click any cell to review it
          </span>
        </div>
      </div>

      {/* ── review panel ────────────────────────────────────────────────── */}
      <aside className="sc-aside">
        <section className="sc-panel">
          <div className="sc-panel-head">
            <span className="material-symbols-outlined">fact_check</span>
            <span className="sc-panel-title">Review cell</span>
            <span className="sc-panel-count">{manualRemaining} to review</span>
          </div>
          {selected && selectedRow && selectedMeta ? (
            <div className="sc-panel-body">
              <div>
                <span className="sc-panel-label">{selectedMeta.label}</span>
                <span className="sc-panel-name">{selectedRow.name}</span>
              </div>
              <span className={`sc-status ${statusToneClass(selected.status)}`}>
                {selected.status || "No result"}
              </span>

              <div>
                <span className="sc-panel-label">Candidate link</span>
                {selected.link ? (
                  <a
                    href={selected.link}
                    target="_blank"
                    rel="noreferrer"
                    className="sc-panel-url"
                  >
                    {selected.link}
                  </a>
                ) : (
                  <span style={{ fontSize: 12, color: "#475569" }}>No profile found.</span>
                )}
              </div>

              {selected.link && (
                <div className={confidenceToneClass(selected.confidence)}>
                  <span className="sc-panel-label">Confidence</span>
                  <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span className="sc-conf-track full">
                      <span
                        className="sc-conf-fill"
                        style={{ width: `${Math.round(selected.confidence * 100)}%` }}
                      />
                    </span>
                    <span className="sc-conf-pct">
                      {Math.round(selected.confidence * 100)}%
                    </span>
                  </span>
                </div>
              )}

              {selected.reason && (
                <div>
                  <span className="sc-panel-label">Reason</span>
                  <p className="sc-panel-reason">{selected.reason}</p>
                </div>
              )}

              {selected.link && (
                <>
                  <div className="sc-panel-actions">
                    <button
                      type="button"
                      className="sc-decide save"
                      aria-pressed={selectedDecision === "verified"}
                      disabled={selectedPending || !db?.connected}
                      onClick={() => void decide(selectedRow.name, selectedMeta.label, "verified")}
                    >
                      <span className="material-symbols-outlined">
                        {selectedDecision === "verified" ? "task_alt" : "bookmark_add"}
                      </span>
                      {selectedDecision === "verified" ? "Saved" : "Save"}
                    </button>
                    <button
                      type="button"
                      className="sc-decide reject"
                      aria-pressed={selectedDecision === "rejected"}
                      disabled={selectedPending || !db?.connected}
                      onClick={() => void decide(selectedRow.name, selectedMeta.label, "rejected")}
                    >
                      <span className="material-symbols-outlined">
                        {selectedDecision === "rejected" ? "block" : "thumb_down"}
                      </span>
                      {selectedDecision === "rejected" ? "Rejected" : "Reject"}
                    </button>
                  </div>
                  <p className="sc-panel-fine">
                    Save writes the URL to <code>verified_url</code>; Reject records it in{" "}
                    <code>rejected_url</code>.
                  </p>
                </>
              )}
            </div>
          ) : (
            <div className="sc-panel-body">
              <p className="sc-panel-reason">
                Click any cell in the grid to see its link, confidence and reason here.
              </p>
            </div>
          )}
        </section>

        <section className="sc-queue">
          <span className="sc-note-label">Review queue</span>
          <span style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
            <span className="sc-queue-n">{manualRemaining}</span>
            <span style={{ fontSize: 11.5, color: "#94a3b8" }}>
              cells still need a human decision
            </span>
          </span>
          <span className="sc-queue-track">
            <span
              style={{
                width: manualTotal ? `${(manualDecided / manualTotal) * 100}%` : "0%",
              }}
            />
          </span>
          <span style={{ display: "block", marginTop: 6, fontSize: 10.5, color: "#64748b" }}>
            {manualDecided} of {manualTotal} resolved
            {decidedTotal > manualDecided &&
              ` · ${decidedTotal - manualDecided} decided outside the queue`}
          </span>
        </section>
      </aside>
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
