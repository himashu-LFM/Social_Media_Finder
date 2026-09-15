"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { AppShell, StageStrip } from "@/components/AppShell";
import { authedFetch } from "@/lib/auth";
import { getPythonApiUrl } from "@/lib/processing-job";
import {
  mapRecordToRow,
  RESULT_PLATFORMS,
  STATUS_VERIFIED,
  STATUS_MANUAL,
  STATUS_WRONG,
  STATUS_NOT_FOUND,
  STATUS_STOPPED,
} from "@/lib/results-mapper";
import type { ResultRow } from "@/types/results";

/**
 * Loads the latest run for the analysis view, in the browser.
 *
 * See ResultsClient for why this cannot be done server-side: the bearer token
 * lives in localStorage and the export files live on the API's disk, not this
 * one's. Client-side loading also makes the static export possible.
 */
function useAnalysisRows(jobId: string) {
  const [rows, setRows] = useState<ResultRow[]>([]);
  const [filename, setFilename] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;

    void (async () => {
      if (!getPythonApiUrl()) {
        if (alive) {
          setLoadError("NEXT_PUBLIC_PYTHON_API_URL is not set.");
          setLoading(false);
        }
        return;
      }
      try {
        // Respect the run selected from History; otherwise the latest run.
        const path = jobId
          ? `/api/results/latest?job_id=${encodeURIComponent(jobId)}`
          : "/api/results/latest";
        const res = await authedFetch(path, { cache: "no-store" });
        if (!alive) return;
        if (!res.ok) {
          setLoadError(`Could not load results (HTTP ${res.status}).`);
          setLoading(false);
          return;
        }
        const data = (await res.json()) as {
          rows?: Record<string, unknown>[];
          filename?: string | null;
        };
        if (alive) {
          setRows((data.rows ?? []).map((r) => mapRecordToRow(r)));
          setFilename(data.filename ?? null);
        }
      } catch (err) {
        if (alive) setLoadError(err instanceof Error ? err.message : "Could not reach the API.");
      } finally {
        if (alive) setLoading(false);
      }
    })();

    return () => {
      alive = false;
    };
  }, [jobId]);

  return { rows, filename, loading, loadError };
}

/** One bucket of the status breakdown, for a stat card and a legend row. */
type Bucket = {
  key: string;
  label: string;
  icon: string;
  tone: string;
  /** Wedge colour, matching the tone but at the donut's opacity. */
  wedge: string;
  count: number;
};

export function AnalysisClient() {
  const searchParams = useSearchParams();
  const jobId = searchParams.get("job") ?? "";
  const { rows, filename, loading, loadError } = useAnalysisRows(jobId);

  // Tag by STATUS, not confidence. The three scored buckets (Verified / Needs
  // Manual Review / Wrong) are the accuracy denominator. Not Found is a real
  // answer ("account confirmed absent") shown separately and EXCLUDED from the
  // score; Not Checked (the run was stopped) asserts nothing at all.
  const stats = useMemo(() => {
    const count = (want: string) =>
      rows.reduce(
        (n, r) => n + RESULT_PLATFORMS.filter((p) => r.platforms[p.key].status === want).length,
        0,
      );
    const verified = count(STATUS_VERIFIED);
    const manual = count(STATUS_MANUAL);
    const wrong = count(STATUS_WRONG);
    const notFound = count(STATUS_NOT_FOUND);
    const notChecked = count(STATUS_STOPPED);
    return {
      verified,
      manual,
      wrong,
      notFound,
      notChecked,
      scored: verified + manual + wrong,
      attempted: verified + manual + wrong + notFound,
    };
  }, [rows]);

  const perPlatform = useMemo(
    () =>
      RESULT_PLATFORMS.map((p) => {
        const of = (want: string) =>
          rows.filter((r) => r.platforms[p.key].status === want).length;
        return {
          ...p,
          v: of(STATUS_VERIFIED),
          m: of(STATUS_MANUAL),
          w: of(STATUS_WRONG),
          n: of(STATUS_NOT_FOUND),
        };
      }),
    [rows],
  );

  const { scored, attempted } = stats;
  const pctScored = (v: number) => (scored ? `${((v / scored) * 100).toFixed(1)}%` : "0.0%");
  const notFoundPct = attempted ? `${((stats.notFound / attempted) * 100).toFixed(1)}%` : "0.0%";

  const buckets: Bucket[] = [
    {
      key: "verified",
      label: "Verified",
      icon: "check_circle",
      tone: "sc-tone-good",
      wedge: "rgba(16,185,129,0.95)",
      count: stats.verified,
    },
    {
      key: "manual",
      label: "Need Manual Review",
      icon: "warning",
      tone: "sc-tone-warn",
      wedge: "rgba(245,158,11,0.95)",
      count: stats.manual,
    },
    {
      key: "wrong",
      label: "Wrong",
      icon: "cancel",
      tone: "sc-tone-bad",
      wedge: "rgba(244,63,94,0.95)",
      count: stats.wrong,
    },
  ];

  // Wedges are laid out in bucket order so the donut and the legend agree.
  let sweep = 0;
  const wedges = buckets.map((b) => {
    const from = sweep;
    sweep += scored ? (b.count / scored) * 360 : 0;
    return `${b.wedge} ${from}deg ${sweep}deg`;
  });
  const donutStyle = {
    background: scored
      ? `conic-gradient(${wedges.join(", ")})`
      : "conic-gradient(rgba(71,85,105,0.5) 0deg 360deg)",
  };

  // The widest review gap is the one worth naming in the footer — it is where
  // an analyst's time goes next.
  const worst = perPlatform.reduce(
    (a, b) => (b.m + b.n > a.m + a.n ? b : a),
    perPlatform[0],
  );

  return (
    <AppShell
      icon="donut_large"
      eyebrow="Status breakdown"
      title="Analysis"
      stages={
        <StageStrip
          current="score"
          noteStrong
          note={
            loading
              ? "Loading…"
              : `${scored} scored links · ${pctScored(stats.verified)} verified`
          }
        />
      }
      actions={
        <>
          <span className="sc-pill">
            <span className="material-symbols-outlined" style={{ fontSize: 14 }}>
              description
            </span>
            {jobId ? "Selected run" : "Latest run"} · {rows.length} rows
          </span>
          <Link
            href={jobId ? `/results?job=${encodeURIComponent(jobId)}` : "/results"}
            className="sc-btn"
          >
            <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
              arrow_back
            </span>
            Back to Results
          </Link>
        </>
      }
    >
      <div className="sc-stack">
        {loadError && (
          <div className="sc-banner sc-tone-warn" role="alert">
            <span className="material-symbols-outlined">warning</span>
            {loadError}
          </div>
        )}

        <section className="sc-row">
          {buckets.map((b) => (
            <span key={b.key} className={`sc-stat ${b.tone}`}>
              <span className="sc-stat-label">
                <span className="material-symbols-outlined">{b.icon}</span>
                {b.label}
              </span>
              <span className="sc-stat-value">
                <span className="sc-stat-n">{b.count}</span>
                <span className="sc-stat-pct">{pctScored(b.count)}</span>
              </span>
            </span>
          ))}
          <span className="sc-stat sc-tone-mute">
            <span className="sc-stat-label">
              <span className="material-symbols-outlined">do_not_disturb_on</span>
              Not Found
            </span>
            <span className="sc-stat-value">
              <span className="sc-stat-n">{stats.notFound}</span>
              <span className="sc-stat-pct">{notFoundPct}</span>
            </span>
          </span>
        </section>

        <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "stretch" }}>
          <section className="sc-donut-card">
            <span
              style={{
                alignSelf: "flex-start",
                fontSize: 10,
                fontWeight: 800,
                letterSpacing: "0.12em",
                textTransform: "uppercase",
                color: "#64748b",
              }}
            >
              Scored links
            </span>
            <span className="sc-donut" style={donutStyle}>
              <span className="sc-donut-hole">
                <span className="sc-donut-n">{scored}</span>
                <span className="sc-donut-cap">Scored links</span>
              </span>
            </span>
            <span className="sc-legend">
              {buckets.map((b) => (
                <span key={b.key} className={`sc-legend-row ${b.tone}`}>
                  <span className="sc-swatch" style={{ background: b.wedge }} />
                  {b.label}
                  <span className="sc-legend-n">{b.count}</span>
                </span>
              ))}
            </span>
            <p
              style={{
                margin: 0,
                alignSelf: "flex-start",
                fontSize: 11,
                lineHeight: 1.6,
                color: "#64748b",
              }}
            >
              Verified, Needs Manual Review and Wrong are scored out of the resolved links. Not
              Found (accounts confirmed absent) is shown separately and excluded from the score.
            </p>
          </section>

          <section
            className="sc-card"
            style={{ flex: "1 1 420px", minWidth: 320, overflow: "hidden" }}
          >
            <div className="sc-card-head">
              <span className="material-symbols-outlined">insights</span>
              <h3 className="sc-card-title">By platform</h3>
              <span className="sc-card-note">{rows.length} rows per platform</span>
            </div>
            <div className="sc-data-wrap">
              <table className="sc-data" style={{ minWidth: 520 }}>
                <thead>
                  <tr>
                    <th>Platform</th>
                    <th>Mix</th>
                    <th className="num" style={{ width: 52, color: "#6ee7b7" }}>
                      Ver
                    </th>
                    <th className="num" style={{ width: 52, color: "#fcd34d" }}>
                      Rev
                    </th>
                    <th className="num" style={{ width: 52, color: "#fda4af" }}>
                      Wrong
                    </th>
                    <th className="num" style={{ width: 52, color: "#94a3b8" }}>
                      N/F
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {perPlatform.map((p) => {
                    const seg = (n: number, color: string) =>
                      rows.length ? (
                        <span
                          key={color}
                          style={{ width: `${(n / rows.length) * 100}%`, background: color }}
                        />
                      ) : null;
                    return (
                      <tr key={p.key}>
                        <td className="name">
                          <span
                            style={{ display: "inline-flex", alignItems: "center", gap: 7 }}
                          >
                            <span
                              className="material-symbols-outlined"
                              style={{ fontSize: 15, color: "#64748b" }}
                            >
                              {p.icon}
                            </span>
                            {p.label}
                          </span>
                        </td>
                        <td>
                          <span className="sc-mix">
                            {seg(p.v, "rgba(16,185,129,0.95)")}
                            {seg(p.m, "rgba(245,158,11,0.95)")}
                            {seg(p.w, "rgba(244,63,94,0.95)")}
                            {seg(p.n, "rgba(100,116,139,0.7)")}
                          </span>
                        </td>
                        <td className="num" style={{ fontWeight: 800, color: "#6ee7b7" }}>
                          {p.v}
                        </td>
                        <td className="num" style={{ fontWeight: 800, color: "#fcd34d" }}>
                          {p.m}
                        </td>
                        <td className="num" style={{ fontWeight: 800, color: "#fda4af" }}>
                          {p.w}
                        </td>
                        <td className="num" style={{ fontWeight: 800, color: "#94a3b8" }}>
                          {p.n}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="sc-foot">
              <span>
                {worst && worst.m + worst.n > 0
                  ? `${worst.label} has the widest review gap — ${worst.m} cells and ${worst.n} confirmed absent.`
                  : loading
                    ? "Loading the latest run…"
                    : "No review gap on any platform."}
              </span>
              <Link
                href={jobId ? `/results?job=${encodeURIComponent(jobId)}` : "/results"}
                className="sc-link"
              >
                Open the review queue
                <span className="material-symbols-outlined" style={{ fontSize: 15 }}>
                  arrow_forward
                </span>
              </Link>
            </div>
          </section>
        </div>

        <section className="sc-banner">
          <span className="sc-foot-item">
            <span className="material-symbols-outlined">pause_circle</span>
            <span>
              <strong style={{ color: "#cbd5e1" }}>{stats.notChecked}</strong> cells Not Checked —
              the run was stopped before those searches ran, so they assert nothing.
            </span>
          </span>
          <span style={{ marginLeft: "auto", color: "#64748b", whiteSpace: "nowrap" }}>
            Export: {filename ?? "none yet"}
          </span>
        </section>
      </div>
    </AppShell>
  );
}
