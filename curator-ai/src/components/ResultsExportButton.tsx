"use client";

import * as XLSX from "xlsx";
import { useToast } from "@/components/ToastProvider";
import type { ResultRow } from "@/types/results";

type Props = {
  rows: ResultRow[];
  /** e.g. Talent_Social_Lookup_20260409_170736.xlsx — used as download filename */
  sourceFileName: string | null;
};

export function ResultsExportButton({ rows, sourceFileName }: Props) {
  const { pushToast } = useToast();

  function download() {
    if (rows.length === 0) {
      pushToast("No rows to export.", "error");
      return;
    }

    const sheetRows = rows.map((r) => ({
      "Talent Name": r.name,
      title_category: r.category,
      title_sub_category: r.subCategory,
      Facebook: r.facebook,
      "Facebook Confidence": r.facebookConfidence,
      Instagram: r.instagram,
      "Instagram Confidence": r.instagramConfidence,
      X: r.x,
      "X Confidence": r.xConfidence,
      TikTok: r.tiktok,
      "TikTok Confidence": r.tiktokConfidence,
      YouTube: r.youtube,
      "YouTube Confidence": r.youtubeConfidence,
      Confidence: r.confidence,
      Source: r.source,
    }));

    const ws = XLSX.utils.json_to_sheet(sheetRows);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, "Social Lookup");

    const fallback = `Talent_Social_Lookup_export_${new Date().toISOString().slice(0, 10)}.xlsx`;
    const name =
      sourceFileName && sourceFileName.endsWith(".xlsx")
        ? sourceFileName.replace(/\.xlsx$/i, "_curator_export.xlsx")
        : fallback;

    XLSX.writeFile(wb, name);
    pushToast("Export ready.", "success");
  }

  return (
    <button
      type="button"
      disabled={rows.length === 0}
      onClick={download}
      className="rounded-xl bg-primary px-5 py-3 text-sm font-bold text-white shadow-xl shadow-primary/30 transition hover:shadow-primary/50 disabled:cursor-not-allowed disabled:opacity-50"
    >
      Export to Excel
    </button>
  );
}
