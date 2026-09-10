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
 * Picks how this run searches — the segmented control from the handoff.
 *
 * With Wikipedia is the tuned default: Wikipedia/Wikidata facts, Serper, LLM
 * verification. Without Wikipedia reads first-party bio links from the file's
 * handles, then searches for whatever is left; nothing on that path is
 * LLM-verified, so every link it finds comes back as Manual Review.
 */
export function SearchModeCard() {
  // The stored config IS the state — the card writes to it and re-reads, so the
  // upload component always sends exactly what the analyst is looking at.
  const config = useSyncExternalStore(
    subscribeSearchConfig,
    getSearchConfigSnapshot,
    getSearchConfigServerSnapshot,
  );
  const isCustom = config.mode === "custom";
  const prompt = config.prompt ?? DEFAULT_PROMPT;

  const setMode = (next: SearchMode) => writeSearchConfig({ ...config, mode: next });

  return (
    <section className="sc-card">
      <div className="sc-card-head">
        <span className="material-symbols-outlined">travel_explore</span>
        <h3 className="sc-card-title">Search mode</h3>
        <div className="sc-segment" role="radiogroup" aria-label="Search mode">
          <button
            type="button"
            role="radio"
            aria-checked={!isCustom}
            onClick={() => setMode("wikipedia")}
          >
            <span className="material-symbols-outlined" style={{ fontSize: 15 }}>
              verified
            </span>
            With Wikipedia
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={isCustom}
            onClick={() => setMode("custom")}
          >
            <span className="material-symbols-outlined" style={{ fontSize: 15 }}>
              edit_note
            </span>
            Without Wikipedia
          </button>
        </div>
      </div>

      {!isCustom ? (
        <p className="sc-muted" style={{ margin: 0, padding: "14px 16px" }}>
          Wikipedia/Wikidata facts, Serper, and LLM verification. Highest precision — use
          it whenever the list has Wikipedia pages. Each row is searched as{" "}
          <code className="sc-code">{"{name} site:{domain}"}</code> and verified against
          its Wikipedia/Wikidata record by the LLM.
        </p>
      ) : (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 12,
            padding: "14px 16px",
          }}
        >
          <p className="sc-muted" style={{ margin: 0 }}>
            No Wikipedia needed. Bio links run first; then a search for the rest — every
            found link is returned as Manual Review.
          </p>

          <label style={{ display: "block" }}>
            <span className="sc-field-label">Custom query prompt</span>
            <input
              type="text"
              spellCheck={false}
              className="sc-input mono"
              value={prompt}
              placeholder={DEFAULT_PROMPT}
              onChange={(e) => writeSearchConfig({ ...config, prompt: e.target.value })}
            />
          </label>

          <label className="sc-check">
            <input
              type="checkbox"
              checked={config.includeProfession}
              onChange={(e) =>
                writeSearchConfig({ ...config, includeProfession: e.target.checked })
              }
            />
            Include profession from the file in the query
          </label>

          <div
            style={{
              padding: "10px 12px",
              borderRadius: 9,
              border: "1px solid rgba(255,255,255,0.08)",
              background: "rgba(2,6,23,0.5)",
            }}
          >
            <span className="sc-field-label" style={{ marginBottom: 5 }}>
              Query sent per talent
            </span>
            <code
              style={{
                display: "block",
                wordBreak: "break-all",
                fontFamily: "ui-monospace, Menlo, monospace",
                fontSize: 12.5,
                color: "#6ee7b7",
              }}
            >
              {previewQuery(prompt, config.includeProfession)}
            </code>
          </div>

          <p className="sc-note warn" style={{ margin: 0 }}>
            <span className="material-symbols-outlined">info</span>
            <span>
              Without a Wikipedia page there are no facts to verify against, so every
              link the search returns comes back as{" "}
              <strong style={{ fontWeight: 800 }}>Manual Review</strong> for a human to
              confirm.
            </span>
          </p>
        </div>
      )}
    </section>
  );
}
