# Curator AI — Talent Social Profile Verification

Given a list of talent, finds and **verifies** their official profiles on Instagram,
Facebook, YouTube, TikTok and X — then labels each one so an analyst only reviews the
cases that genuinely need a human.

Every cell is `Verified`, `Manual Review Needed`, `Wrong`, `Not Found` or `Not Checked`.
The system is deliberately biased toward Manual Review: a blank is cheap, a confidently
wrong profile in a client dataset is not.

## Layout

```
api_server.py               FastAPI: jobs, cancellation, results, analyst decisions
verification_pipeline.py    Orchestrator — the entry point for a run
├── wikipedia_service.py      ground truth  (structured identity from Wikipedia/Wikidata)
│   └── wikidata_lookup.py      QID resolution, claims, social properties
├── serper_service.py         discovery     (site: search + evidence lookup, cached per run)
├── apify_service.py          discovery     (batched backup for unresolved platforms)
├── profile_metadata.py       evidence      (OpenGraph: bio, followers, display name)
├── verification_service.py   adjudication  (LLM verdict + deterministic guards)
├── social_urls.py            platform URL rules — single source of truth
└── excel_service.py          input parsing + formatted workbook output
db_service.py               analyst decisions -> Postgres (verified_url / rejected_url)
retry_util.py               shared HTTP retry/backoff

curator-ai/                 Next.js UI — discovery, live progress, results, analysis
sql/                        schema to run in pgAdmin (run 001 first)
data/                       sample inputs
exports/                    generated workbooks (gitignored)
```

## How a run works

1. **Ground truth** — the Wikipedia URL (or IMDb id, or a guarded name search) resolves a
   Wikidata entity: professions, works, nationality, birth year, declared handles.
2. **Discovery** — one `"<name>" site:<domain>` search per platform, plus any handle the
   input file already supplies, plus that handle reused across platforms if search finds
   nothing.
3. **Evidence** — Serper snippets and knowledge graph, plus OpenGraph profile data
   (bio, follower count, display name) where the platform exposes it.
4. **Adjudication** — the LLM picks at most one candidate, then deterministic guards run.
5. **Backup** — platforms still unresolved get one batched Apify pass and are re-judged.
6. **Output** — a colour-coded workbook, plus a companion "Serper-only" workbook showing
   what search alone produced.

### Guards that can only downgrade

The LLM never has the last word on a `Verified`. Each of these can move a verdict to
Manual Review, never the reverse, so a guard misfiring costs coverage but never precision:

| Guard | Catches |
|---|---|
| Existence probe | Handles that 404 (YouTube, X) |
| Evidence floor | Candidates we retrieved nothing about |
| Third-party framing | "this page is about…", "unofficial", "fan page" |
| Name-order mismatch | "Scully N James" for "James Scully" |
| Follower plausibility | Three-figure followings for notable subjects |
| Fan-handle shape | `name_1`, `name_official`, with no supporting content |
| Ambiguity guard | Several live accounts claiming the same identity |
| Thin ground truth | Name-only identity that cannot rule out a namesake |

## Setup

**Backend** — from the repo root:

```bash
python -m pip install -r requirements.txt
copy .env.example .env        # then fill in the keys
uvicorn api_server:app --host 127.0.0.1 --port 8787 --reload
```

**Frontend**:

```bash
cd curator-ai
npm install
copy .env.example .env.local  # set NEXT_PUBLIC_PYTHON_API_URL=http://127.0.0.1:8787
npm run dev
```

**Database** (optional — enables Save / Reject on the Results page). Run
`sql/001_create_url_tables.sql` in pgAdmin, then set `DATABASE_URL` in `.env`.

> On Windows, `pip.exe` may be blocked by Application Control. Use `python -m pip` instead.

## Environment

| Variable | Required | Purpose |
|---|---|---|
| `SERPER_API_KEY` | yes | Search — the primary discovery and evidence source |
| `OPENAI_API_KEY` | yes* | LLM adjudication |
| `ANTROPIC_API_KEY` | no | Claude as the primary LLM; OpenAI becomes the fallback |
| `APIFY_TOKEN` / `APIFY_ACTOR_ID` | no | Backup discovery for unresolved platforms |
| `DATABASE_URL` | no | Postgres for analyst decisions |
| `CORS_ORIGINS` | prod | Comma-separated frontend origins |
| `EXPORT_DIR` | no | Where workbooks are written (default `exports/`) |
| `PIPELINE_ROW_WORKERS` | no | Rows processed concurrently (default 4) |
| `SERPER_CANDIDATES_PER_PLATFORM` | no | Candidates fetched per platform (default 4) |
| `AMBIGUITY_GUARD` | no | Set `0` to disable the same-name guard |

\* At least one LLM key is required. Without any, every cell falls back to Manual Review.

## Input

Any `.xlsx` / `.xls` / `.csv`. Column headers are detected rather than fixed:

- **Name** — `Talent Name`, `Title`, or the first column
- **Wikipedia URL** — any header containing "wiki", or detected from cell contents
- **Existing handles** — `instagram_user`, `facebook_page`, `twitter_handle`,
  `tiktok_user`, `youtube_channel_username`. These are treated as candidates to
  **verify**, not re-discovered — worth roughly a third of the work on a typical file.
- Every other column becomes identity metadata for the verifier.

## Known limitations

- **Wikipedia-anchored.** Talent with no Wikidata entity get thin ground truth and are
  capped at Manual Review by design. Supplying `imdb_id` helps; nothing fully replaces it.
- **Instagram, TikTok and X expose little metadata**, so those columns carry more Manual
  Reviews than Facebook and YouTube.
- **Runs are not yet fully reproducible.** Google returns different results for identical
  queries, and the Anthropic path does not pin `temperature`. Expect some cell-level churn
  between two runs of the same input.
- **Accuracy is not formally measured.** There is no labelled evaluation set yet; the
  analyst decisions captured in `verified_url` / `rejected_url` are intended to become one.
