# Knowledge Transfer — Social Media Finder (Curator AI)

**Author:** Ram Goyal · **Prepared for:** the next developer taking over this project
**Last updated:** 2026-08-18 · **Active branch:** `non_wikiram`

> This document explains what the project does, how it is built, how to run it,
> and the important gotchas — so you can pick it up without needing me. Read the
> "Big Picture" and "Two Workflows" sections first; everything else is reference.

---

## 1. Big Picture — what this project does

**Input:** an Excel/CSV file with a list of talents (people/brands), one per row —
name, an optional Wikipedia URL, a profession/category, and optionally some
existing social handles.

**Output:** the same rows, now filled with each talent's **official social media
profiles** across 5 platforms — **Instagram, Facebook, YouTube, TikTok, X (Twitter)** —
each tagged with a **status** (Verified / Manual Review / Wrong / Not Found), a
**confidence**, a **reason**, and a **source** (where the link came from). Exported
as an `.xlsx`.

It is a **talent → official social profiles resolver + verifier.**

---

## 2. Tech Stack

| Layer | Tech |
|---|---|
| **Frontend** | Next.js 16 (App Router, Turbopack), React 19, TypeScript, Tailwind CSS 4 |
| **Backend** | Python, FastAPI, Uvicorn, pandas / numpy / openpyxl, pydantic, python-dotenv |
| **LLM** | Anthropic Claude (primary), OpenAI gpt-4o-mini (fallback) |
| **Search / discovery** | Serper.dev, SerpApi (Google AI Mode), Apify actor `tri_angle~social-media-finder` |
| **Identity data** | Wikipedia REST API + Wikidata |
| **Database (optional)** | PostgreSQL via `psycopg` — for auth + analyst decisions. Off by default. |

**Ports:** backend runs on `127.0.0.1:8787`, frontend on `localhost:3000`.

---

## 3. Two Workflows (THE most important section)

Every row is processed in one of two modes, chosen by the user on the Discovery
page (**With Wikipedia** vs **Without Wikipedia**).

### 3A. Wikipedia mode (`mode = "wikipedia"`, the default)
For talents that HAVE a Wikipedia page. This is the tuned, high-precision path.

```
Excel row (name + Wikipedia URL)
  1. GROUND TRUTH  — fetch structured facts from Wikidata + Wikipedia summary
                     (occupation, nationality, genre, known works, official site,
                     Spotify/IMDb IDs). NEVER sends the full article to the LLM.
  2. DISCOVER      — Serper "<name> site:instagram.com" per platform, top results
  3. ENRICH        — pull profile context/OG tags (followers, bio, verified badge)
  4. VERIFY (LLM)  — compare each candidate vs ground truth → Verified / Wrong / Manual
  5. APIFY BACKUP  — only for platforms still not Verified; re-verify; keep the best
```
- **LLM is used** as the verifier.
- Labels: Verified / Wrong / Manual Review / Not Found.

### 3B. Custom / Non-Wikipedia mode (`mode = "custom"`)
For the majority of a typical client file — talents with NO Wikipedia page.
**This path uses NO LLM, NO Serper, NO Apify.**

```
Excel row (name + profession)
  Phase 0 — FIRST-PARTY BIO LINKS
      Take the Instagram/YouTube handle from the file (if present) as an anchor,
      scrape that page for links to OTHER platforms.
      Anything found → VERIFIED (100%). The anchor itself → Verified.
      (Reality: Instagram yields ~nothing anonymously; YouTube's About page works.)

  SerpApi FILL — for every platform Phase 0 did NOT fill:
      ONE SerpApi Google-AI-Mode query: "<Name> [<Profession>] <prompt>"
      Extract the links Google cites → tag MANUAL REVIEW NEEDED.
      Nothing found → NOT FOUND.
```
- **No verification** — there is no ground truth to verify against, so every
  SerpApi link is handed to a human (Manual Review).
- The analyst types a free-text **prompt** (e.g. "social media handles") and can
  toggle whether the **profession** (from the file) is included in the query.
- `<Name>` and `<Profession>` come from the Excel; only the prompt is typed.
- Labels: Verified (Phase 0 only) / Manual Review Needed / Not Found.

> **Why two modes?** With a Wikipedia page you have facts to verify against, so you
> can produce trustworthy "Verified" labels. Without one, you can only *surface*
> candidates for a human — so Non-Wiki mode does exactly that.

---

## 4. How to Run It Locally

You need **two terminals** (backend + frontend).

### Terminal 1 — Backend
```bash
# from the project root: D:\Desktop\QuintProject2\Social_Media_Finder
python -m pip install -r requirements.txt          # first time only
python -m uvicorn api_server:app --host 127.0.0.1 --port 8787 --reload
```
Success: `Uvicorn running on http://127.0.0.1:8787`.

### Terminal 2 — Frontend
```bash
cd curator-ai
npm install                                        # first time only
npm run dev
```
Open **http://localhost:3000**.

The frontend needs `NEXT_PUBLIC_PYTHON_API_URL=http://127.0.0.1:8787` in
`curator-ai/.env.local`.

---

## 5. Environment Variables (`.env` in project root — GITIGNORED, never commit)

```
# LLM (OpenAI fallback is currently the active one; Anthropic is commented out)
OPENAI_API_KEY=...
OPENAI_CHAT_MODEL=gpt-4o-mini
#ANTROPIC_API_KEY=...        # uncomment to enable Claude as primary

# Apify (Wikipedia-mode backup discovery)
APIFY_TOKEN=...
APIFY_ACTOR_ID=tri_angle~social-media-finder

# Serper (Wikipedia-mode primary discovery + X)
SERPER_API_KEY=...

# SerpApi (Non-Wiki mode Google AI Mode discovery)
SERPAPI_API_KEY=...
SERPAPI_ENGINE=google_ai_mode
SERPAPI_QUERY_SUFFIX=social media handles   # default prompt (optional)

# Frontend CORS
CORS_ORIGINS=http://localhost:3000

# Database (OPTIONAL — leave unset to disable auth + decision storage)
# DATABASE_URL=postgresql://user:pass@host:5432/dbname
```

> **Note:** the LLM only affects **Wikipedia mode**. Non-Wiki mode uses no LLM,
> so those keys don't matter for that path. Anthropic (`ANTROPIC_API_KEY` — note
> the spelling used in the file) is commented out, so verification currently runs
> on OpenAI `gpt-4o-mini`.

---

## 6. Key Files (backend)

| File | Responsibility |
|---|---|
| `api_server.py` | FastAPI app. Endpoints for upload, jobs, results, history, auth, DB health. Background-thread job model. |
| `verification_pipeline.py` | **The orchestrator.** Row routing, the two workflows, all phases, Excel assembly. Start here. |
| `search_options.py` | The run config: `mode` (wikipedia/custom), `prompt`, `include_profession`. |
| `serpapi_service.py` | Non-Wiki discovery. SerpApi Google AI Mode + 4-layer link extractor. |
| `serper_service.py` | Wikipedia-mode discovery (`<name> site:domain`) + context extraction + query templating. |
| `apify_service.py` | Apify backup discovery (Wikipedia mode). |
| `wikipedia_service.py` | Wikidata/Wikipedia structured ground-truth extraction (NO LLM, NO full article). |
| `wikidata_lookup.py` | Low-level Wikidata QID resolution + entity fetch. |
| `verification_service.py` | The LLM verifier (Anthropic→OpenAI). Prompt, JSON parsing, labels, guards. |
| `bio_link_service.py` | Phase 0 first-party bio-link harvesting from anchor profiles. |
| `profile_metadata.py` | OG-tag / public profile scraping (followers, bio, verified badge). |
| `social_urls.py` | Single source of truth for the 5 platforms + URL validation/normalization. |
| `excel_service.py` | Input parsing + output schema (columns) + save. |
| `db_service.py` | Optional Postgres: analyst verified/rejected decisions, uploads, runs. |
| `auth_service.py` | Optional auth (only enforced when DATABASE_URL is set). |
| `retry_util.py` | Shared pooled HTTP session + retry/backoff. |

### Key files (frontend, under `curator-ai/src`)
| File | Responsibility |
|---|---|
| `app/discovery/page.tsx` | The upload page. |
| `components/DiscoveryWorkspace.tsx` | Upload widget; sends the file + search config. |
| `components/SearchModeCard.tsx` | The With/Without Wikipedia toggle + custom prompt UI. |
| `lib/search-mode.ts` | Stores the mode/prompt/include-profession config (localStorage). |
| `app/results/page.tsx` | Results view. |
| `app/processing/page.tsx` | Live per-row progress. |

---

## 7. Output Excel Schema

For each of the 5 platforms, 5 columns:

```
<Platform> | <Platform> Status | <Platform> Source | <Platform> Confidence | <Platform> Reason
```
Plus `Talent Name`, `Wikipedia URL`, and an overall `Confidence`.

**Status values:** `Verified`, `Manual Review Needed`, `Wrong`, `Not Found`.

**Source values (where the link came from):**
- Non-Wiki: `SerpApi (Google AI Mode)`, `Phase 0 (Instagram bio)`, `Phase 0 (YouTube bio)`, `Input file (Phase 0 anchor)`
- Wikipedia: `Serper + LLM`, `Apify + LLM`, `Input file + LLM`, `Analyst (saved decision)`, `Bio backlink (via …)`

Output files are saved to `exports/Talent_Social_Lookup_<timestamp>.xlsx`.

---

## 8. Database (OPTIONAL — currently OFF)

The pipeline runs fully WITHOUT a database. Postgres only adds:
- **Auth** (sign-in) — enforced only when `DATABASE_URL` is set.
- **Analyst decisions** — Verified/Rejected profiles persist across runs
  (`verified_url` / `rejected_url` tables).

To enable: create a Postgres DB, run `sql/001_create_url_tables.sql` once, set
`DATABASE_URL` in `.env`, `pip install "psycopg[binary,pool]"`, and check
`http://127.0.0.1:8787/api/db/health`.

---

## 9. Important Gotchas & Limitations (READ THIS)

1. **Google AI Mode (SerpApi) is non-deterministic.** The same non-Wiki input can
   return different links on different runs — it's a live AI answer, not a fixed
   lookup. This is expected, not a bug.

2. **Instagram scraping (Phase 0) almost always returns nothing.** Instagram
   serves logged-out fetches a stripped page with no bio links. This is by
   Instagram's design and cannot be fixed with a better User-Agent. **YouTube's
   About page is the reliable anchor.** Do NOT build a dependency on Instagram
   scraping — treat it as a bonus. It fails soft (returns `{}`, row continues).

3. **SerpApi budget is the real scaling limit**, not blocking. ~1 call per non-Wiki
   row. The current plan is 250 searches/month — a 234-row file ≈ the whole month.
   SerpApi handles Google-side blocking, so you never get IP-banned; you just run
   out of quota. Upgrade the plan for big files.

4. **Anonymous scraping degrades gracefully.** If Instagram/YouTube block a fetch,
   the row falls through to SerpApi. Nothing crashes.

5. **The 4-layer SerpApi extractor** (in `serpapi_service.py`) handles Google's
   inconsistent response shapes: (1) full `snippet_links` URLs, (2) bare domain +
   handle in text, (3) regex over the payload, (4) handles named only in prose.

6. **Only 5 platforms are supported** (IG/FB/YT/TikTok/X). Telegram, Snapchat,
   LinkedIn, Threads, Linktree are dropped. To add one, edit `social_urls.PLATFORMS`
   + validation + the Excel schema in `excel_service.py`.

---

## 10. Branches & Testing

- **Active branch:** `non_wikiram` (has the Phase 0 + SerpApi non-Wiki flow).
- Older work lived on `ram_temp` / `main`. `serpapi_service.py` was recovered from
  commit `f0ba0c3`.
- **Tests:** `python -m pytest tests/ -q` (currently 149 passing). Key files:
  `tests/test_search_modes.py`, `tests/test_bio_links.py`,
  `tests/test_nonwiki_accuracy.py`, `tests/test_guards.py`,
  `tests/test_urls_and_schema.py`.

---

## 11. Where to Extend (open ideas / TODOs)

- **Reliable Instagram links:** wire Apify's Instagram profile scraper into Phase 0
  (paid, but absorbs blocking) — the only durable way to read IG bio links.
- **Throttle/backoff** the Phase 0 YouTube fetch for high-volume runs.
- **Enable Claude:** uncomment `ANTROPIC_API_KEY` for better Wikipedia-mode verification.
- **Add Telegram** as a 6th platform (see gotcha #6).
- **Surface the Source column in the Results UI** (currently Excel-only).
- **Retry-on-empty** for SerpApi rows that return 0 links (mitigates non-determinism).
- **Dead code:** custom-mode Serper helpers (`_tag_anchor_handle_matches`,
  `_tag_backlinks`, `_adopt_backlink_discoveries`) are now unused in custom mode
  (custom uses SerpApi). They remain guarded for Wikipedia mode. Safe to leave or clean up.

---

## 12. Quick Start for the New Owner

1. Clone, checkout `non_wikiram`.
2. Create `.env` (root) with the keys from Section 5, and `curator-ai/.env.local`
   with `NEXT_PUBLIC_PYTHON_API_URL=http://127.0.0.1:8787`.
3. Run backend + frontend (Section 4).
4. Open http://localhost:3000 → Discovery → pick a mode → upload an Excel.
5. Read `verification_pipeline.py` — `run_pipeline_on_dataframe()` is the entry
   point; follow `_phase_a` to see both workflows branch on `options.is_custom`.

**Questions during handover:** ram@listenfirstmedia.com / ramgoyal2005@gmail.com
