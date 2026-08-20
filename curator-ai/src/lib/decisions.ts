/** Client for the analyst decision endpoints (verified_url / rejected_url). */
import { getPythonApiUrl } from "@/lib/processing-job";
import { authedFetch } from "@/lib/auth";

export type DecisionKind = "verified" | "rejected";

/** {lowercased title: {verified: {platform: url}, rejected: {platform: url}}} */
export type DecisionMap = Record<
  string,
  { verified: Record<string, string>; rejected: Record<string, string> }
>;

export type DbHealth = { configured: boolean; connected: boolean; detail: string };

type DecisionPayload = {
  title: string;
  platform: string;
  url: string;
  title_category?: string;
  title_subcategory?: string;
};

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await authedFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    // FastAPI puts the useful message in `detail` — surface it, not the status.
    let detail = `Request failed (${res.status})`;
    try {
      const j = (await res.json()) as { detail?: string };
      if (j?.detail) detail = j.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export function recordDecision(kind: DecisionKind, payload: DecisionPayload) {
  return post<{ ok: boolean }>(`/api/decisions/${kind === "verified" ? "verify" : "reject"}`, payload);
}

export function lookupDecisions(titles: string[]) {
  return post<{ decisions: DecisionMap }>("/api/decisions/lookup", { titles });
}

export async function checkDbHealth(): Promise<DbHealth | null> {
  const api = getPythonApiUrl();
  if (!api) return null;
  try {
    const res = await authedFetch("/api/db/health", { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as DbHealth;
  } catch {
    return null;
  }
}
