/** Session keys for Discovery → Processing → Results flow (client-only). */

export const PROCESSING_NAMES_KEY = "curator-ai-processing-names-v1";
export const PROCESSING_RUN_FINISHED_KEY = "curator-ai-processing-run-finished-v1";
export const PYTHON_JOB_ID_KEY = "curator-ai-python-job-id-v1";

/**
 * Base URL of the FastAPI app in C:\\Testing (no trailing slash). Set in .env.local.
 * Must not depend on `window` — reading NEXT_PUBLIC_* must match on server and client
 * or Client Components (e.g. DiscoveryFileUpload) will hydration-mismatch.
 */
export function getPythonApiUrl(): string | null {
  const u = process.env.NEXT_PUBLIC_PYTHON_API_URL?.trim().replace(/\/$/, "");
  return u && u.length > 0 ? u : null;
}

export function setPythonJobId(id: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (id) sessionStorage.setItem(PYTHON_JOB_ID_KEY, id);
    else sessionStorage.removeItem(PYTHON_JOB_ID_KEY);
  } catch {
    /* ignore */
  }
}

export function readPythonJobId(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return sessionStorage.getItem(PYTHON_JOB_ID_KEY);
  } catch {
    return null;
  }
}

export function parseNamesFromText(text: string, ignoreSingleNameLines: boolean): string[] {
  const lines = text
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (!ignoreSingleNameLines) return lines;
  return lines.filter((line) => line.split(/\s+/).length > 1);
}

export function saveProcessingNames(names: string[]): void {
  if (typeof window === "undefined") return;
  try {
    sessionStorage.setItem(PROCESSING_NAMES_KEY, JSON.stringify(names));
    sessionStorage.removeItem(PROCESSING_RUN_FINISHED_KEY);
  } catch {
    /* quota or private mode */
  }
}

export function readProcessingNames(): string[] | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = sessionStorage.getItem(PROCESSING_NAMES_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return null;
    return parsed.filter((x): x is string => typeof x === "string" && x.length > 0);
  } catch {
    return null;
  }
}

export function markProcessingRunFinished(): void {
  if (typeof window === "undefined") return;
  try {
    sessionStorage.setItem(PROCESSING_RUN_FINISHED_KEY, "1");
  } catch {
    /* ignore */
  }
}

export function hasProcessingRunFinished(): boolean {
  if (typeof window === "undefined") return false;
  return sessionStorage.getItem(PROCESSING_RUN_FINISHED_KEY) === "1";
}
