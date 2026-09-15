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

/**
 * Downloads the workbook the BACKEND produced — the client's own brand-definition
 * report, filled in place (sure links written into the platform columns, uncertain
 * ones in the appended review columns). We deliberately do NOT rebuild a sheet in
 * the browser: only the server has the original file to write back into.
 */
export function ResultsExportButton({ rows }: Props) {
  const { pushToast } = useToast();
  const [busy, setBusy] = useState(false);

  function filenameFrom(res: Response): string {
    const cd = res.headers.get("content-disposition") || "";
    const match = /filename\*?=(?:UTF-8'')?"?([^;"]+)"?/i.exec(cd);
    return match?.[1]?.trim() || `Talent_Social_Lookup_${new Date().toISOString().slice(0, 10)}.xlsx`;
  }

  async function download() {
    if (rows.length === 0) {
      pushToast("No results to export yet.", "error");
      return;
    }
    setBusy(true);
    try {
      const jobId = readPythonJobId();
      const path = jobId
        ? `/api/export/latest?job_id=${encodeURIComponent(jobId)}`
        : "/api/export/latest";
      const res = await authedFetch(path);

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
      pushToast("Export downloaded.", "success");
    } catch {
      pushToast("Could not reach the server to download the export.", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      disabled={rows.length === 0 || busy}
      onClick={download}
      className="sc-btn accent"
    >
      <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
        download
      </span>
      {busy ? "Preparing…" : "Export XLSX"}
    </button>
  );
}
