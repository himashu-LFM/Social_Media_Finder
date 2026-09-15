"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { authedFetch } from "@/lib/auth";
import { getPythonApiUrl } from "@/lib/processing-job";
import {
  confidenceToneClass,
  mapRecordToRow,
  RESULT_PLATFORMS,
  statusToneClass,
  STATUS_MANUAL,
  STATUS_NOT_FOUND,
  STATUS_VERIFIED,
} from "@/lib/results-mapper";
import type { PlatformKey, ResultRow } from "@/types/results";

/**
 * The Serper-only (Phase A) export — what Serper + the LLM produced BEFORE the
 * Apify backup and cross-platform corroboration.
 *
 * Loaded in the browser. The previous server-side version could not work once
 * deployed: it authenticated with a token held in localStorage (absent on the
 * server) and fell back to reading workbooks off the local filesystem, which
 * the frontend container does not share with the API.
 */
function useSerperRows(jobId: string) {
  const [rows, setRows] = useState<ResultRow[]>([]);
  const [latestFileName, setLatestFileName] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadWarning, setLoadWarning] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);

    void (async () => {
      if (!getPythonApiUrl()) {
        if (alive) {
          setLoadError("NEXT_PUBLIC_PYTHON_API_URL is not set.");
          setLoading(false);
        }
        return;
      }
      try {
        const url = jobId
          ? `/api/results/serper/latest?job_id=${encodeURIComponent(jobId)}`
          : "/api/results/serper/latest";
        const res = await authedFetch(url, { cache: "no-store" });
        if (!alive) return;
        if (!res.ok) {
          setLoadError(`Could not load the Serper export (HTTP ${res.status}).`);
          setLoading(false);
          return;
        }
        const data = (await res.json()) as {
          rows?: Record<string, unknown>[];
          filename?: string | null;
          warning?: string | null;
          error?: string | null;
          pending?: boolean;
        };
        if (!alive) return;
        if (data.pending) {
          setRows([]);
          setLoadWarning("This run is still processing.");
        } else {
          setRows((data.rows ?? []).map((r) => mapRecordToRow(r)));
          setLatestFileName(data.filename ?? null);
          setLoadWarning(data.warning ?? null);
          setLoadError(data.error ?? null);
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

  return { rows, latestFileName, loadError, loadWarning, loading };
}

/** One talent × platform candidate — the unit this page lists. */
type Candidate = {
  key: string;
  name: string;
  platform: string;
  platformKey: PlatformKey;
  icon: string;
  status: string;
  link: string;
  confidence: number;
  reason: string;
};

export function SerperClient() {
  const searchParams = useSearchParams();
  const jobId = searchParams.get("job") ?? "";
  const { rows, latestFileName, loadError, loadWarning, loading } = useSerperRows(jobId);
  const [query, setQuery] = useState("");

  // The export is a grid of talents × platforms; this page reads it as a flat
  // list of candidates, which is how it is actually reviewed. Cells with no
  // candidate link carry no information here, so they are left out.
  const candidates = useMemo<Candidate[]>(
    () =>
      rows.flatMap((r) =>
        RESULT_PLATFORMS.filter((p) => r.platforms[p.key].link).map((p) => ({
          key: `${r.name}|${p.key}`,
          name: r.name,
          platform: p.label,
          platformKey: p.key,
          icon: p.icon,
          status: r.platforms[p.key].status,
          link: r.platforms[p.key].link,
          confidence: r.platforms[p.key].confidence,
          reason: r.platforms[p.key].reason,
        })),
      ),
    [rows],
  );

  const totalCells = rows.length * RESULT_PLATFORMS.length;
  const counts = useMemo(() => {
    const all = rows.flatMap((r) => RESULT_PLATFORMS.map((p) => r.platforms[p.key].status));
    return {
      verified: all.filter((s) => s === STATUS_VERIFIED).length,
      review: all.filter((s) => s === STATUS_MANUAL).length,
      notFound: all.filter((s) => s === STATUS_NOT_FOUND).length,
    };
  }, [rows]);

  const q = query.trim().toLowerCase();
  const visible = q ? candidates.filter((c) => c.name.toLowerCase().includes(q)) : candidates;

  return (
    <AppShell
      icon="travel_explore"
      eyebrow="Phase A export"
      title="Serper only"
      actions={
        <>
          <span className="sc-pill mono">
            <span className="material-symbols-outlined" style={{ fontSize: 14 }}>
              description
            </span>
            {loading ? "Loading…" : (latestFileName ?? "no export yet")}
          </span>
          <Link
            href={jobId ? `/results?job=${encodeURIComponent(jobId)}` : "/results"}
            className="sc-btn"
          >
            <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
              compare_arrows
            </span>
            Compare with final
          </Link>
        </>
      }
      stages={
        <div className="sc-subbar">
          <span className="sc-foot-item" style={{ whiteSpace: "normal" }}>
            <span
              className="material-symbols-outlined"
              style={{ fontSize: 16, color: "rgba(242,209,0,0.85)" }}
            >
              info
            </span>
            <span>
              What Serper and the LLM produced{" "}
              <strong style={{ color: "#e2e8f0" }}>before</strong> the Apify backup and
              cross-platform corroboration.
            </span>
          </span>
          <span
            style={{
              marginLeft: "auto",
              display: "inline-flex",
              flexWrap: "wrap",
              alignItems: "center",
              gap: 12,
              whiteSpace: "nowrap",
            }}
          >
            <span>{rows.length} rows</span>
            <span className="sc-foot-item sc-tone-good">
              <span className="sc-dot" />
              {counts.verified} verified
            </span>
            <span className="sc-foot-item sc-tone-warn">
              <span className="sc-dot" />
              {counts.review} review
            </span>
            <span className="sc-foot-item sc-tone-mute">
              <span className="sc-dot" />
              {counts.notFound} not found
            </span>
          </span>
        </div>
      }
    >
      <div className="sc-stack">
        {(loadError || loadWarning) && (
          <div
            className={`sc-banner ${loadError ? "sc-tone-warn" : "sc-tone-info"}`}
            role={loadError ? "alert" : undefined}
          >
            <span className="material-symbols-outlined">{loadError ? "warning" : "info"}</span>
            {loadError ?? loadWarning}
          </div>
        )}

        <section className="sc-banner sc-tone-info">
          <span className="sc-foot-item" style={{ whiteSpace: "normal" }}>
            <span className="material-symbols-outlined">layers</span>
            <span>
              Phase A resolved <strong>{candidates.length}</strong> of {totalCells} cells. The
              Apify backup and cross-platform corroboration run after this — compare with the
              final export to see what they changed.
            </span>
          </span>
          <Link
            href={jobId ? `/results?job=${encodeURIComponent(jobId)}` : "/results"}
            className="sc-link"
            style={{ marginLeft: "auto" }}
          >
            See the final export
            <span className="material-symbols-outlined" style={{ fontSize: 15 }}>
              arrow_forward
            </span>
          </Link>
        </section>

        <section className="sc-card" style={{ overflow: "hidden" }}>
          <div className="sc-card-head">
            <span className="material-symbols-outlined">table_chart</span>
            <h3 className="sc-card-title">Serper candidates</h3>
            <span className="sc-card-note">
              Read-only — no decisions are recorded on this view
            </span>
            <span className="sc-search" style={{ marginLeft: "auto" }}>
              <span className="material-symbols-outlined">search</span>
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search talent name…"
                aria-label="Search talent name"
                style={{ width: 190, height: 28 }}
              />
            </span>
          </div>

          <div className="sc-data-wrap">
            <table className="sc-data" style={{ minWidth: 900 }}>
              <thead>
                <tr>
                  <th>Talent</th>
                  <th style={{ width: 110 }}>Platform</th>
                  <th style={{ width: 132 }}>Status</th>
                  <th>Candidate link</th>
                  <th className="num" style={{ width: 96 }}>
                    Confidence
                  </th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((c) => (
                  <tr key={c.key}>
                    <td className="name">{c.name}</td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "#cbd5e1" }}>
                        <span
                          className="material-symbols-outlined"
                          style={{ fontSize: 15, color: "#64748b" }}
                        >
                          {c.icon}
                        </span>
                        {c.platform}
                      </span>
                    </td>
                    <td>
                      <span className={`sc-status ${statusToneClass(c.status)}`}>
                        {c.status || "—"}
                      </span>
                    </td>
                    <td>
                      <a
                        href={c.link}
                        target="_blank"
                        rel="noreferrer"
                        className="url"
                        title={c.link}
                      >
                        {c.link.replace(/^https?:\/\/(www\.)?/, "")}
                      </a>
                    </td>
                    <td className={confidenceToneClass(c.confidence)}>
                      <span className="sc-conf">
                        <span className="sc-conf-track">
                          <span
                            className="sc-conf-fill"
                            style={{ width: `${Math.round(c.confidence * 100)}%` }}
                          />
                        </span>
                        <span className="sc-conf-pct">{Math.round(c.confidence * 100)}%</span>
                      </span>
                    </td>
                    <td className="why" title={c.reason}>
                      {c.reason}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {loading && <div className="sc-empty">Loading the Serper export…</div>}
            {!loading && visible.length === 0 && (
              <div className="sc-empty">
                <span className="material-symbols-outlined">
                  {candidates.length === 0 ? "inbox" : "filter_alt_off"}
                </span>
                {candidates.length === 0
                  ? "No Serper output yet. Run the pipeline to generate a Talent_Social_Serper_*.xlsx file."
                  : `No candidate matches “${query.trim()}”.`}
              </div>
            )}
          </div>

          <div className="sc-foot">
            <span style={{ whiteSpace: "nowrap" }}>
              Showing <strong>{visible.length}</strong> of {totalCells} cells
            </span>
            <span style={{ whiteSpace: "nowrap" }}>
              Cells with no candidate link are omitted from this export
            </span>
          </div>
        </section>
      </div>
    </AppShell>
  );
}
