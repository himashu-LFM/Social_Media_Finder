import type { Metadata } from "next";
import { AppShell, StageStrip } from "@/components/AppShell";
import { DiscoveryWorkspace } from "@/components/DiscoveryWorkspace";
import { SearchModeCard } from "@/components/SearchModeCard";

export const metadata: Metadata = {
  title: "Discovery | Social Scout",
  description: "Upload a talent list and start a verification run.",
};

const OUTPUT_COLUMNS = [
  { label: "Talent Name", icon: "person" },
  { label: "Wikipedia URL", icon: "link" },
  { label: "Instagram", icon: "photo_camera" },
  { label: "Facebook", icon: "groups" },
  { label: "YouTube", icon: "smart_display" },
  { label: "TikTok", icon: "music_note" },
  { label: "X / Twitter", icon: "alternate_email" },
  { label: "+ Status (each)", icon: "verified" },
  { label: "+ Confidence (each)", icon: "percent" },
  { label: "+ Reason (each)", icon: "notes" },
];

const PIPELINE = [
  "Build a rich Wikipedia/Wikidata ground-truth profile (no full page sent to the LLM).",
  "Apify Social Media Finder discovers Instagram, Facebook, YouTube, TikTok links.",
  "Serper extracts context for each link, and finds missing platforms (incl. X).",
  "LLM verifies each profile: Verified / Wrong / Manual Review Needed.",
  "Link + status + confidence + reason written to the XLSX export.",
];

export default function DiscoveryPage() {
  return (
    <AppShell
      icon="dashboard"
      eyebrow="Talent resolver"
      title="Discovery"
      stages={<StageStrip current="search" note="No run in progress" />}
      actions={
        <>
          <span className="sc-pill">
            <span
              className="material-symbols-outlined"
              style={{ fontSize: 14, color: "#34d399" }}
            >
              bolt
            </span>
            API connected
          </span>
          <a href="/history" className="sc-btn">
            <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
              history
            </span>
            Recent runs
          </a>
        </>
      }
    >
      <div className="sc-page">
        <div className="sc-col">
          <SearchModeCard />
          <DiscoveryWorkspace />

          <section className="sc-card" style={{ overflow: "hidden" }}>
            <div className="sc-card-head">
              <span className="material-symbols-outlined">table_view</span>
              <h3 className="sc-card-title">Spreadsheet format</h3>
              <span className="sc-card-note">Row 1 = headers · one talent per row</span>
            </div>
            <div className="sc-table-scroll">
              <table className="sc-table">
                <thead>
                  <tr>
                    <th>Column</th>
                    <th style={{ padding: "8px 12px" }}>Example</th>
                    <th>Notes</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td style={{ whiteSpace: "nowrap" }}>
                      <code>Talent Name</code>
                      <span className="sc-tag-required">required</span>
                    </td>
                    <td style={{ padding: "11px 12px", fontWeight: 600, color: "#e2e8f0" }}>
                      Jake Thompson
                    </td>
                    <td>
                      One person per row. Also accepts: Talent, Name, Title (or first
                      column).
                    </td>
                  </tr>
                  <tr>
                    <td style={{ whiteSpace: "nowrap" }}>
                      <code>Wikipedia URL</code>
                      <span className="sc-tag-optional">optional</span>
                    </td>
                    <td
                      style={{
                        padding: "11px 12px",
                        wordBreak: "break-all",
                        fontFamily: "ui-monospace, Menlo, monospace",
                        fontSize: 11,
                        color: "#cbd5e1",
                      }}
                    >
                      https://en.wikipedia.org/wiki/Jake_Thompson
                    </td>
                    <td>
                      Recommended. Structured identity metadata (profession, nationality,
                      aliases, known works) is extracted from it to verify each profile.
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <section className="sc-card">
            <div className="sc-card-head">
              <span className="material-symbols-outlined">view_column</span>
              <h3 className="sc-card-title">Expected output columns</h3>
            </div>
            <div className="sc-card-body sc-chip-row">
              {OUTPUT_COLUMNS.map((c) => (
                <span key={c.label} className="sc-chip-lg">
                  <span className="material-symbols-outlined">{c.icon}</span>
                  {c.label}
                </span>
              ))}
            </div>
          </section>
        </div>

        <aside className="sc-aside">
          <section className="sc-card">
            <div className="sc-card-head" style={{ padding: "12px 14px" }}>
              <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
                account_tree
              </span>
              <span
                style={{
                  fontSize: 10,
                  fontWeight: 800,
                  letterSpacing: "0.14em",
                  textTransform: "uppercase",
                  color: "#f2d100",
                }}
              >
                Pipeline logic
              </span>
            </div>
            <ol className="sc-steps">
              {PIPELINE.map((text, i) => (
                <li key={text}>
                  <span className="sc-step-n">{String(i + 1).padStart(2, "0")}</span>
                  <span className="sc-step-text">{text}</span>
                </li>
              ))}
            </ol>
          </section>

          <section className="sc-note good">
            <span className="sc-note-label">
              <span className="material-symbols-outlined" style={{ fontSize: 15 }}>
                bolt
              </span>
              Bio links run first
            </span>
            <p>
              If a row has an Instagram or YouTube handle in the file, that profile is
              read once and any platform it links to is confirmed straight away — no
              search, no cost.
            </p>
          </section>

          <section className="sc-note">
            <span className="sc-note-label">Tip</span>
            <p>
              A <code className="sc-code">Wikipedia URL</code> greatly improves accuracy —
              its structured metadata anchors identity so each candidate profile is
              verified against the right person. Without it, the pipeline falls back to a
              best-effort name search.
            </p>
          </section>
        </aside>
      </div>
    </AppShell>
  );
}
