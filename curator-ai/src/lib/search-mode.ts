/**
 * Which search a run should use — mirrors search_options.py on the backend.
 *
 * The choice lives in localStorage rather than component state because the
 * analyst picks it on the discovery page and the upload happens in a sibling
 * component; keeping it in one place also means a returning analyst gets the
 * query they wrote last time instead of retyping it.
 */

export type SearchMode = "wikipedia" | "custom";

const MODE_KEY = "curator-ai-search-mode-v1";
const TEMPLATE_KEY = "curator-ai-search-template-v1";

/** What Wikipedia mode sends to Google today. Shown as the starting point. */
export const DEFAULT_QUERY_TEMPLATE = "{name} site:{domain}";

export const PLACEHOLDERS = [
  { token: "{name}", label: "Talent name", example: "Kako Fujita" },
  { token: "{domain}", label: "Platform domain", example: "instagram.com" },
  { token: "{platform}", label: "Platform name", example: "Instagram" },
  { token: "{category}", label: "Title category from the file", example: "Talent" },
  { token: "{subcategory}", label: "Title subcategory from the file", example: "Musician" },
] as const;

export type SearchConfig = { mode: SearchMode; queryTemplate: string };

export function readSearchConfig(): SearchConfig {
  if (typeof window === "undefined") return { mode: "wikipedia", queryTemplate: "" };
  try {
    const mode = localStorage.getItem(MODE_KEY) === "custom" ? "custom" : "wikipedia";
    return { mode, queryTemplate: localStorage.getItem(TEMPLATE_KEY) ?? DEFAULT_QUERY_TEMPLATE };
  } catch {
    return { mode: "wikipedia", queryTemplate: "" };
  }
}

export function writeSearchConfig(config: SearchConfig): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(MODE_KEY, config.mode);
    localStorage.setItem(TEMPLATE_KEY, config.queryTemplate);
  } catch {
    /* private mode */
  }
  cached = config;
  for (const listener of listeners) listener();
}

// ── external store, so the card can read localStorage without a setState-in-
// effect (which cascades a render) and without an SSR hydration mismatch: the
// server snapshot is the default, and React swaps in the stored value itself.

const SERVER_SNAPSHOT: SearchConfig = { mode: "wikipedia", queryTemplate: DEFAULT_QUERY_TEMPLATE };
let cached: SearchConfig | null = null;
const listeners = new Set<() => void>();

export function subscribeSearchConfig(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Must return a stable reference between calls or React re-renders forever. */
export function getSearchConfigSnapshot(): SearchConfig {
  if (!cached) cached = readSearchConfig();
  return cached;
}

export function getSearchConfigServerSnapshot(): SearchConfig {
  return SERVER_SNAPSHOT;
}

/** Attach the current mode to an outgoing run. Wikipedia mode sends nothing. */
export function applySearchConfig(fd: FormData, config = readSearchConfig()): void {
  if (config.mode !== "custom") return;
  fd.append("search_mode", "custom");
  fd.append("query_template", config.queryTemplate.trim());
}

/**
 * Same checks the API runs, so the analyst sees the problem while typing rather
 * than as a rejected upload. Returns "" when the template is usable.
 */
export function validateTemplate(template: string): string {
  const t = template.trim();
  if (!t) return "Enter a search query, or switch back to Wikipedia mode.";
  if (!t.includes("{name}"))
    return "The query must include {name}, or every row would search for the same thing.";
  if (t.length > 300) return "That query is too long for a Google search.";
  // An unrecognised token is left as literal text by the renderer, so a typo
  // like {platfrom} would end up in every query in the file.
  const known = new Set<string>(PLACEHOLDERS.map((p) => p.token));
  const unknown = [...new Set(t.match(/\{[^{}]*\}/g) ?? [])].filter((tok) => !known.has(tok));
  if (unknown.length)
    return (
      `Unknown placeholder${unknown.length > 1 ? "s" : ""} ${unknown.sort().join(", ")}. ` +
      `Available: ${PLACEHOLDERS.map((p) => p.token).join(", ")}.`
    );
  return "";
}

/** Render the template the way serper_service.build_query does, for the preview. */
export function previewQuery(
  template: string,
  values: Partial<Record<string, string>> = {},
): string {
  const filled: Record<string, string> = {
    name: values.name ?? "Kako Fujita",
    domain: values.domain ?? "instagram.com",
    platform: values.platform ?? "Instagram",
    category: values.category ?? "Talent",
    subcategory: values.subcategory ?? "Musician",
  };
  let out = template || DEFAULT_QUERY_TEMPLATE;
  for (const [key, value] of Object.entries(filled)) {
    out = out.split(`{${key}}`).join(value);
  }
  return out.replace(/\s+/g, " ").trim();
}
