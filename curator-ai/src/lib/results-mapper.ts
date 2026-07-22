/**
 * Shared helpers for turning a workbook / API record into a typed ResultRow.
 * Used by both the Results and Analysis pages so the mapping lives in one place.
 */
import type { PlatformKey, ResultRow } from "@/types/results";

/** Verification labels (must match verification_service.py). */
export const STATUS_VERIFIED = "Verified";
export const STATUS_WRONG = "Wrong";
export const STATUS_MANUAL = "Manual Review Needed";
export const STATUS_NOT_FOUND = "Not Found";

/** Platform metadata driving column order, labels, and workbook keys. */
export const RESULT_PLATFORMS: {
  key: PlatformKey;
  label: string;
  column: string;
  icon: string;
}[] = [
  { key: "instagram", label: "Instagram", column: "Instagram", icon: "photo_camera" },
  { key: "facebook", label: "Facebook", column: "Facebook", icon: "groups" },
  { key: "youtube", label: "YouTube", column: "YouTube", icon: "smart_display" },
  { key: "tiktok", label: "TikTok", column: "TikTok", icon: "music_note" },
  { key: "x", label: "X", column: "X", icon: "alternate_email" },
];

export function asString(v: unknown): string {
  if (v === undefined || v === null) return "";
  return String(v).trim();
}

export function asConfidence(v: unknown): number {
  if (typeof v === "number") {
    return v > 1 ? Math.max(0, Math.min(1, v / 100)) : Math.max(0, Math.min(1, v));
  }
  const raw = String(v ?? "").trim();
  if (!raw) return 0;
  const cleaned = raw.replace(/,/g, "").replace(/%$/, "");
  const n = Number(cleaned);
  if (!Number.isFinite(n)) return 0;
  // Treat 0..1 as already-normalized, 1..100 as percent points.
  if (n > 1) return Math.max(0, Math.min(1, n / 100));
  return Math.max(0, Math.min(1, n));
}

export function mapRecordToRow(r: Record<string, unknown>): ResultRow {
  const platforms = {} as ResultRow["platforms"];
  for (const p of RESULT_PLATFORMS) {
    platforms[p.key] = {
      link: asString(r[p.column]),
      status: asString(r[`${p.column} Status`]),
      confidence: asConfidence(r[`${p.column} Confidence`]),
      reason: asString(r[`${p.column} Reason`]),
    };
  }
  return {
    name: asString(r["Talent Name"]),
    wikipediaUrl: asString(r["Wikipedia URL"]),
    platforms,
    confidence: asConfidence(r["Confidence"]),
  };
}

/** Tailwind classes for a status badge, keyed by verification label. */
export function statusTone(status: string): string {
  switch (status) {
    case STATUS_VERIFIED:
      return "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30";
    case STATUS_MANUAL:
      return "bg-amber-500/10 text-amber-300 ring-amber-500/30";
    case STATUS_WRONG:
      return "bg-rose-500/10 text-rose-300 ring-rose-500/30";
    default:
      return "bg-slate-500/10 text-slate-400 ring-slate-500/20";
  }
}
