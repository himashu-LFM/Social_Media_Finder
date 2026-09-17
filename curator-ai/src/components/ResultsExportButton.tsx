"use client";

import { useState } from "react";
import { useToast } from "@/components/ToastProvider";
import { authedFetch } from "@/lib/auth";
import { readPythonJobId } from "@/lib/processing-job";
import type { ResultRow } from "@/types/results";

type Props = {
  rows: ResultRow[];
  /** Unused now — the file is produced server-side. Kept for API compatibility. */
  sourceFileName: string | null;
};

type ExportFormat = "compact" | "full";

/**
 * Downloads the workbook the BACKEND produced. Two shapes:
 *   • compact (default) — brand_id, name, and a link + status per platform, plus
 *     one reason column. The everyday analyst sheet.
 *   • full — the client's own brand-definition report filled in place (or the
 *     tool's wide schema for a names-only run).
 * We deliberately do NOT rebuild a sheet in the browser: only the server has the
 * original file and the per-platform verdicts.
 */
export function ResultsExportButton({ rows }: Props) {
  const { pushToast } = useToast();
  const [busy, setBusy] = useState<ExportFormat | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);

  function filenameFrom(res: Response): string {
    const cd = res.headers.get("content-disposition") || "";
    const match = /filename\*?=(?:UTF-8'')?"?([^;"]+)"?/i.exec(cd);
    return match?.[1]?.trim() || `Talent_Social_${new Date().toISOString().slice(0, 10)}.xlsx`;
  }

  async function download(format: ExportFormat) {
    setMenuOpen(false);
    if (rows.length === 0) {
      pushToast("No results to export yet.", "error");
      return;
    }
    setBusy(format);
    try {
      const jobId = readPythonJobId();
      const params = new URLSearchParams({ format });
      if (jobId) params.set("job_id", jobId);
      const res = await authedFetch(`/api/export/latest?${params.toString()}`);

      if (res.status === 409) {
        pushToast("That run is still processing — try again in a moment.", "error");
        return;
      }
      if (res.status === 404) {
        pushToast("No export is available for this run yet.", "error");
        return;
      }
      if (!res.ok) {
        pushToast("Could not download the export. Please try again.", "error");
        return;
      }

      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filenameFrom(res);
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      pushToast(
        format === "compact" ? "Compact export downloaded." : "Full report downloaded.",
        "success",
      );
    } catch {
      pushToast("Could not reach the server to download the export.", "error");
    } finally {
      setBusy(null);
    }
  }

  const disabled = rows.length === 0 || busy !== null;

  return (
    <div className="sc-split">
      <button
        type="button"
        disabled={disabled}
        onClick={() => download("compact")}
        className="sc-btn accent sc-split-main"
      >
        <span
          className={`material-symbols-outlined ${busy === "compact" ? "sc-spin" : ""}`}
          style={{ fontSize: 16 }}
        >
          {busy === "compact" ? "progress_activity" : "download"}
        </span>
        {busy === "compact" ? "Preparing…" : "Export XLSX"}
      </button>
      <button
        type="button"
        disabled={disabled}
        aria-label="More export options"
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        onClick={() => setMenuOpen((o) => !o)}
        className="sc-btn accent sc-split-caret"
      >
        <span
          className={`material-symbols-outlined ${busy === "full" ? "sc-spin" : ""}`}
          style={{ fontSize: 18 }}
        >
          {busy === "full" ? "progress_activity" : "expand_more"}
        </span>
      </button>

      {menuOpen && (
        <>
          <div className="sc-menu-backdrop" aria-hidden onClick={() => setMenuOpen(false)} />
          <div className="sc-menu" role="menu">
            <button
              type="button"
              role="menuitem"
              className="sc-menu-item"
              onClick={() => download("compact")}
            >
              <span className="material-symbols-outlined" style={{ color: "#f2d100" }}>
                table_rows
              </span>
              <span>
                <span className="sc-menu-item-title">
                  Compact sheet <span style={{ color: "#64748b", fontWeight: 600 }}>(default)</span>
                </span>
                <span className="sc-menu-item-note">
                  brand_id, name, link + status per platform, one reason column.
                </span>
              </span>
            </button>
            <button
              type="button"
              role="menuitem"
              className="sc-menu-item"
              onClick={() => download("full")}
            >
              <span className="material-symbols-outlined" style={{ color: "#94a3b8" }}>
                description
              </span>
              <span>
                <span className="sc-menu-item-title">Full report</span>
                <span className="sc-menu-item-note">
                  Your original columns filled in place, for the brand-definition report.
                </span>
              </span>
            </button>
          </div>
        </>
      )}
    </div>
  );
}
