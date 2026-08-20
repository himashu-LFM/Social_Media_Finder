"use client";

import { useSyncExternalStore } from "react";
import {
  DEFAULT_PROMPT,
  getSearchConfigServerSnapshot,
  getSearchConfigSnapshot,
  previewQuery,
  subscribeSearchConfig,
  writeSearchConfig,
  type SearchMode,
} from "@/lib/search-mode";

/**
 * Picks how this run searches.
 *
 * Wikipedia mode is the tuned default: Wikipedia/Wikidata facts, Serper, LLM
 * verification. Custom mode is for lists with no Wikipedia page — it reads any
 * first-party bio links from the file's handles, then runs a SerpApi Google AI
 * Mode query "<Name> <Profession> <prompt>" for whatever is left. Name and
 * Profession come from the file; the analyst only types the prompt.
 */
export function SearchModeCard() {
  // The stored config IS the state — the card writes to it and re-reads, so the
  // upload component always sends exactly what the analyst is looking at.
  const config = useSyncExternalStore(
    subscribeSearchConfig,
    getSearchConfigSnapshot,
    getSearchConfigServerSnapshot,
  );
  const mode = config.mode;
  const isCustom = mode === "custom";
  const prompt = config.prompt ?? DEFAULT_PROMPT;

  const setMode = (next: SearchMode) => writeSearchConfig({ ...config, mode: next });
  const setPrompt = (next: string) => writeSearchConfig({ ...config, prompt: next });
  const setIncludeProfession = (next: boolean) =>
    writeSearchConfig({ ...config, includeProfession: next });

  return (
    <div className="lf-enter lf-enter-delay-1 lf-card relative overflow-hidden p-6">
      <div className="absolute -left-10 -top-10 h-32 w-32 rounded-full bg-primary/10 blur-3xl" />
      <div className="relative z-10">
        <div className="mb-3 flex items-center gap-2">
          <span className="material-symbols-outlined text-primary">travel_explore</span>
          <h3 className="text-lg font-bold text-slate-100">Search mode</h3>
        </div>

        {/* ── the toggle ── */}
        <div
          role="radiogroup"
          aria-label="Search mode"
          className="mb-4 grid gap-2 sm:grid-cols-2"
        >
          <ModeOption
            selected={!isCustom}
            onSelect={() => setMode("wikipedia")}
            icon="verified"
            title="With Wikipedia"
            blurb="Wikipedia/Wikidata facts, Serper, and LLM verification. Highest precision — use it whenever the list has Wikipedia pages."
          />
          <ModeOption
            selected={isCustom}
            onSelect={() => setMode("custom")}
            icon="edit_note"
            title="Without Wikipedia"
            blurb="No Wikipedia needed. Bio links run first; then a Google AI Mode search for the rest — every found link is returned as Manual Review."
          />
        </div>

        {isCustom ? (
          <div className="space-y-3">
            <label
              htmlFor="lf-custom-prompt"
              className="block text-xs font-semibold uppercase tracking-wide text-slate-500"
            >
              Custom query prompt
            </label>
            <input
              id="lf-custom-prompt"
              type="text"
              spellCheck={false}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder={DEFAULT_PROMPT}
              className="w-full rounded-xl border border-slate-800 bg-slate-950/70 px-3 py-2.5 font-mono text-sm text-slate-100 outline-none transition focus:border-primary/50"
            />

            <label className="flex cursor-pointer items-center gap-2 text-xs text-slate-300">
              <input
                type="checkbox"
                checked={config.includeProfession}
                onChange={(e) => setIncludeProfession(e.target.checked)}
                className="h-4 w-4 accent-[color:var(--color-primary,#f2d100)]"
              />
              Include profession from the file in the query
            </label>

            <div className="rounded-xl border border-slate-800 bg-slate-950/50 px-3 py-2.5">
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                Query sent per talent
              </p>
              <code className="block break-all font-mono text-sm text-emerald-300">
                {previewQuery(prompt, config.includeProfession)}
              </code>
              <p className="mt-2 text-xs leading-relaxed text-slate-500">
                <span className="font-semibold text-slate-400">Name</span> and{" "}
                <span className="font-semibold text-slate-400">Profession</span> are pulled from
                each row&apos;s Excel data; only the prompt is yours to type.
              </p>
            </div>

            <p className="flex items-start gap-2 rounded-xl border border-emerald-500/25 bg-emerald-500/10 px-3 py-2.5 text-xs leading-relaxed text-emerald-100">
              <span className="material-symbols-outlined text-base">bolt</span>
              <span>
                <strong className="font-bold">Bio links run first.</strong> If a row has an
                Instagram or YouTube handle in the file, that profile is read once and any
                platform it links to is confirmed straight away (Verified) — no search, no cost.
                The query above only runs for whatever is left.
              </span>
            </p>

            <p className="flex items-start gap-2 rounded-xl border border-amber-500/25 bg-amber-500/10 px-3 py-2.5 text-xs leading-relaxed text-amber-100">
              <span className="material-symbols-outlined text-base">info</span>
              <span>
                Without a Wikipedia page there are no facts to verify against, so every link the
                Google AI Mode search returns comes back as <strong>Manual Review</strong> for a
                human to confirm — there is no LLM verification on this path.
              </span>
            </p>
          </div>
        ) : (
          <p className="text-sm leading-relaxed text-slate-400">
            Each row is searched as{" "}
            <code className="rounded bg-slate-950/80 px-1.5 py-0.5 font-mono text-slate-300">
              {"{name} site:{domain}"}
            </code>{" "}
            and verified against its Wikipedia/Wikidata record by the LLM.
          </p>
        )}
      </div>
    </div>
  );
}

function ModeOption({
  selected,
  onSelect,
  icon,
  title,
  blurb,
}: {
  selected: boolean;
  onSelect: () => void;
  icon: string;
  title: string;
  blurb: string;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={onSelect}
      className={`rounded-xl border p-3.5 text-left transition ${
        selected
          ? "border-primary/50 bg-primary/10"
          : "border-slate-800 bg-slate-950/40 hover:border-slate-700"
      }`}
    >
      <span className="mb-1 flex items-center gap-2">
        <span
          className={`material-symbols-outlined text-lg ${
            selected ? "text-primary" : "text-slate-500"
          }`}
        >
          {icon}
        </span>
        <span className={`font-bold ${selected ? "text-slate-100" : "text-slate-300"}`}>
          {title}
        </span>
      </span>
      <span className="block text-xs leading-relaxed text-slate-500">{blurb}</span>
    </button>
  );
}
