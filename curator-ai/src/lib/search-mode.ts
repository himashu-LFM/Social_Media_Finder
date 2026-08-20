/**
 * Which search a run should use — mirrors search_options.py on the backend.
 *
 * The choice lives in localStorage rather than component state because the
 * analyst picks it on the discovery page and the upload happens in a sibling
 * component; keeping it in one place also means a returning analyst gets the
 * prompt they wrote last time instead of retyping it.
 *
 * Wikipedia mode = the tuned Wikipedia/Wikidata + Serper + LLM pipeline.
 * Custom mode    = first-party bio links (Phase 0) + SerpApi Google AI Mode for
 *                  whatever is left, with the query "<Name> [<Profession>] <prompt>".
 *                  Name and Profession are pulled from the file; the analyst only
 *                  types the prompt.
 */

export type SearchMode = "wikipedia" | "custom";

const MODE_KEY = "curator-ai-search-mode-v1";
const PROMPT_KEY = "curator-ai-search-prompt-v1";
const INCLUDE_PROF_KEY = "curator-ai-search-include-profession-v1";

/** The default custom-mode prompt, matching the backend default suffix. */
export const DEFAULT_PROMPT = "social media handles";

export type SearchConfig = {
  mode: SearchMode;
  prompt: string;
  includeProfession: boolean;
};

export function readSearchConfig(): SearchConfig {
  if (typeof window === "undefined")
    return { mode: "wikipedia", prompt: DEFAULT_PROMPT, includeProfession: true };
  try {
    const mode = localStorage.getItem(MODE_KEY) === "custom" ? "custom" : "wikipedia";
    const prompt = localStorage.getItem(PROMPT_KEY) ?? DEFAULT_PROMPT;
    // Absent means "not set yet" → default ON. Only an explicit "false" turns it off.
    const includeProfession = localStorage.getItem(INCLUDE_PROF_KEY) !== "false";
    return { mode, prompt, includeProfession };
  } catch {
    return { mode: "wikipedia", prompt: DEFAULT_PROMPT, includeProfession: true };
  }
}

export function writeSearchConfig(config: SearchConfig): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(MODE_KEY, config.mode);
    localStorage.setItem(PROMPT_KEY, config.prompt);
    localStorage.setItem(INCLUDE_PROF_KEY, config.includeProfession ? "true" : "false");
  } catch {
    /* private mode */
  }
  cached = config;
  for (const listener of listeners) listener();
}

// ── external store, so the card can read localStorage without a setState-in-
// effect (which cascades a render) and without an SSR hydration mismatch: the
// server snapshot is the default, and React swaps in the stored value itself.

const SERVER_SNAPSHOT: SearchConfig = {
  mode: "wikipedia",
  prompt: DEFAULT_PROMPT,
  includeProfession: true,
};
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
  fd.append("prompt", config.prompt.trim());
  fd.append("include_profession", config.includeProfession ? "true" : "false");
}

/**
 * Render the query exactly as the backend builds it, for the UI preview:
 * "<Name> [<Profession>] <prompt>". Name and Profession are placeholders here
 * (they come from each row's Excel data at run time).
 */
export function previewQuery(
  prompt: string,
  includeProfession: boolean,
  exampleName = "Kako Fujita",
  exampleProfession = "Musician",
): string {
  const parts = [
    exampleName,
    includeProfession ? exampleProfession : "",
    prompt.trim(),
  ].filter((p) => p);
  return parts.join(" ").replace(/\s+/g, " ").trim();
}
