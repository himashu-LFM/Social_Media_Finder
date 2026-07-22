"use client";

import * as XLSX from "xlsx";
import { useToast } from "@/components/ToastProvider";
import { RESULT_PLATFORMS } from "@/lib/results-mapper";
import type { ResultRow } from "@/types/results";

type Props = {
  rows: ResultRow[];
  /** e.g. Talent_Social_Lookup_20260409_170736.xlsx — used as download filename */
  sourceFileName: string | null;
};

/** Confidence is stored normalized (0..1); the workbook schema uses 0-100. */
function toPercent(value: number): number | "" {
  return value > 0 ? Math.round(value * 100) : "";
}

export function ResultsExportButton({ rows, sourceFileName }: Props) {
  const { pushToast } = useToast();

  function download() {
    if (rows.length === 0) {
      pushToast("No rows to export.", "error");
      return;
    }

    const sheetRows = rows.map((r) => {
      const row: Record<string, string | number> = {
        "Talent Name": r.name,
        "Wikipedia URL": r.wikipediaUrl,
      };
      for (const p of RESULT_PLATFORMS) {
        const cell = r.platforms[p.key];
        row[p.column] = cell.link;
        row[`${p.column} Status`] = cell.status;
        row[`${p.column} Confidence`] = toPercent(cell.confidence);
        row[`${p.column} Reason`] = cell.reason;
      }
      row["Confidence"] = toPercent(r.confidence);
      return row;
    });

    const ws = XLSX.utils.json_to_sheet(sheetRows);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, "Social Lookup");

    const fallback = `Talent_Social_Lookup_export_${new Date().toISOString().slice(0, 10)}.xlsx`;
    const name =
      sourceFileName && sourceFileName.endsWith(".xlsx")
        ? sourceFileName.replace(/\.xlsx$/i, "_listenfirst_export.xlsx")
        : fallback;

    XLSX.writeFile(wb, name);
    pushToast("Export ready.", "success");
  }

  return (
    <button
      type="button"
      disabled={rows.length === 0}
      onClick={download}
      className="lf-btn-primary inline-flex items-center gap-2 px-5 py-3 text-sm disabled:cursor-not-allowed disabled:opacity-50"
    >
      <span className="material-symbols-outlined text-lg">download</span>
      Export to Excel
    </button>
  );
}
