"use client";

import { useSyncExternalStore } from "react";
import {
  DEFAULT_QUERY_TEMPLATE,
  getSearchConfigServerSnapshot,
  getSearchConfigSnapshot,
  PLACEHOLDERS,
  previewQuery,
  subscribeSearchConfig,
  validateTemplate,
  writeSearchConfig,
  type SearchMode,
} from "@/lib/search-mode";

/**
 * Picks how this run searches.
 *
 * Wikipedia mode is the tuned default and is described honestly: it is stricter,
 * and that strictness is why its Verified labels are trustworthy. Custom mode is
 * for the lists Wikipedia does not cover, where the analyst knows the
 * disambiguating term and the tool does not.
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
  const template = config.queryTemplate || DEFAULT_QUERY_TEMPLATE;

  const setMode = (next: SearchMode) => writeSearchConfig({ ...config, mode: next });
  const setTemplate = (next: string | ((prev: string) => string)) =>
    writeSearchConfig({
      ...config,
      queryTemplate: typeof next === "function" ? next(template) : next,
    });

  const problem = mode === "custom" ? validateTemplate(template) : "";
  const isCustom = mode === "custom";

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
            title="Wikipedia"
            blurb="Wikipedia/Wikidata identity facts, standard site: query. Highest precision — use it whenever the list has Wikipedia pages."
          />
          <ModeOption
            selected={isCustom}
            onSelect={() => setMode("custom")}
            icon="edit_note"
            title="Custom query"
            blurb="No Wikipedia page needed. You write the Google query, and confirmation requires strong profile evidence instead."
          />
        </div>

        {isCustom ? (
          <div className="space-y-3">
            <label
              htmlFor="lf-query-template"
              className="block text-xs font-semibold uppercase tracking-wide text-slate-500"
            >
              Serper query
            </label>
            <textarea
              id="lf-query-template"
              rows={2}
              spellCheck={false}
              value={template}
              onChange={(e) => setTemplate(e.target.value)}
              aria-invalid={problem ? true : undefined}
              aria-describedby="lf-query-help"
              className={`w-full resize-y rounded-xl border bg-slate-950/70 px-3 py-2.5 font-mono text-sm text-slate-100 outline-none transition focus:border-primary/50 ${
                problem ? "border-rose-500/50" : "border-slate-800"
              }`}
            />

            <div className="flex flex-wrap gap-1.5">
              {PLACEHOLDERS.map((p) => (
                <button
                  key={p.token}
                  type="button"
                  title={`${p.label} — e.g. ${p.example}`}
                  onClick={() => setTemplate((t) => `${t.trimEnd()} ${p.token}`.trim())}
                  className="rounded-lg border border-slate-800 bg-slate-950/60 px-2 py-1 font-mono text-xs text-slate-400 transition hover:border-primary/40 hover:text-primary"
                >
                  {p.token}
                </button>
              ))}
            </div>

            {problem ? (
              <p className="flex items-start gap-2 text-sm text-rose-300" role="alert">
                <span className="material-symbols-outlined text-base">error</span>
                {problem}
              </p>
            ) : (
              <div id="lf-query-help" className="rounded-xl border border-slate-800 bg-slate-950/50 px-3 py-2.5">
                <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Instagram search for an example row
                </p>
                <code className="block break-all font-mono text-sm text-emerald-300">
                  {previewQuery(template)}
                </code>
                <p className="mt-2 text-xs leading-relaxed text-slate-500">
                  Runs once per platform, with {"{domain}"} and {"{platform}"} swapped for each.
                  Placeholders with no value in the file are dropped.
                </p>
              </div>
            )}

            <p className="flex items-start gap-2 rounded-xl border border-amber-500/25 bg-amber-500/10 px-3 py-2.5 text-xs leading-relaxed text-amber-100">
              <span className="material-symbols-outlined text-base">info</span>
              <span>
                Without a Wikipedia page there are no identity facts to rule out a namesake, so
                custom mode confirms a profile only when it returns real evidence (bio, following,
                knowledge panel) and the model is clearly confident. Everything else still comes
                back as Manual Review — expect more of it than in Wikipedia mode.
              </span>
            </p>
          </div>
        ) : (
          <p className="text-sm leading-relaxed text-slate-400">
            Each row is searched as{" "}
            <code className="rounded bg-slate-950/80 px-1.5 py-0.5 font-mono text-slate-300">
              {previewQuery(DEFAULT_QUERY_TEMPLATE)}
            </code>{" "}
            and checked against its Wikipedia/Wikidata record.
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
