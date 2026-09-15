# Curator AI — Talent Social Profile Finder & Verifier

Given a list of talent, finds their official profiles on **Instagram, Facebook,
YouTube, TikTok and X** — and labels each one so an analyst only reviews the
cases that genuinely need a human.

Every cell is `Verified`, `Manual Review Needed`, `Wrong`, `Not Found` or
`Not Checked`. The system is deliberately biased toward Manual Review: a blank is
cheap, a confidently wrong profile in a client dataset is not.

> `Not Checked` is distinct from `Not Found` on purpose. "Not Found" asserts an
> absence we actually looked for; "Not Checked" says we never got to look (the run
> was stopped, or search was unavailable). Collapsing the two would let a failed
> run be ingested downstream as "this person has no TikTok".

---

## Contents

1. [The two workflows](#1-the-two-workflows) — read this first
2. [Running it locally](#2-running-it-locally)
3. [Environment variables](#3-environment-variables)
4. [Input format](#4-input-format)
5. [Accounts and sign-in](#5-accounts-and-sign-in)
6. [Code layout](#6-code-layout)
7. [Guards](#7-guards-that-can-only-downgrade)
8. [Deployment](#8-deployment)
9. [Known limitations](#9-known-limitations)

---

## 1. The two workflows

Every row runs in one of two modes, chosen on the Discovery page
(**With Wikipedia** / **Without Wikipedia**). They exist because the honest answer
differs: with a Wikipedia page you have facts to verify against and can produce a
trustworthy "Verified"; without one you can only *surface* candidates for a human.

### 1A. Wikipedia mode (`mode = "wikipedia"`, the default)

The tuned, high-precision path. Uses the LLM.

```
Excel row (name + Wikipedia URL)
  1. GROUND TRUTH  Wikidata + Wikipedia summary — occupation, nationality, genre,
                   known works, official site, Spotify/IMDb IDs.
                   The full article is NEVER sent to the LLM.
  2. DISCOVER      Serper "<name> site:<domain>" per platform, plus any handle the
                   input file supplies, plus that handle reused across platforms
                   if search finds nothing.
  3. ENRICH        OpenGraph profile data — bio, follower count, display name.
  4. VERIFY (LLM)  One request per platform ranks all candidates, then the
                   deterministic guards run.
  5. APIFY BACKUP  Only for platforms still not Verified; re-judged.
  6. CORROBORATE   Handles confirmed on one platform rescue Manual Reviews on others.
```

### 1B. Without-Wikipedia mode (`mode = "custom"`)

The majority of a typical client file. **No LLM, no Wikipedia lookup.**

```
Excel row (name + profession + any handles)
  PHASE 0 — FIRST-PARTY LINKS                                    → Verified
      a. Every handle the file supplies (all 5 platforms). The client asserts
         these; re-searching them would waste spend and wrongly demote them.
      b. Bio links read off the Instagram/YouTube anchor profile.
      c. Link aggregators (Linktree, Beacons, bio.link, …) followed one hop.
      d. Instagram bio via the Apify actor — PAID, so it runs last and only for
         the gaps a–c left. Anonymous Instagram publishes almost nothing; this is
         the only reliable reader.

  SERPER FILL — for every platform Phase 0 did not resolve      → Manual Review
      One "<name> [<profession>] site:<domain>" search per missing platform.
      Links are handed through unverified for a human to confirm.
      Search unavailable → Not Checked (never a false Not Found).
```

**Why Phase 0 links are trusted without verification:** a link published in a
profile's own bio was put there by the account holder. That is first-party
evidence, and stronger than anything a search result can provide. Each such cell
records the anchor it came from in its Reason, so the assumption stays auditable.

---

## 2. Running it locally

Two terminals.

**Backend** — from the repo root:

```bash
python -m pip install -r requirements.txt
cp .env.example .env        # then fill in the keys — see below
uvicorn app.main:app --host 127.0.0.1 --port 8787 --reload
```

**Frontend**:

```bash
cd curator-ai
npm install
cp .env.example .env.local
npm run dev
```

Open <http://localhost:3000>. The frontend needs
`NEXT_PUBLIC_PYTHON_API_URL=http://127.0.0.1:8787` in `curator-ai/.env.local`.

**Database** (optional — enables sign-in and Save/Reject on Results). Run the
`sql/` scripts in order, set `DATABASE_URL`, then create the first account:

```bash
python scripts/create_user.py
```

It prompts interactively, so the password never reaches your shell history.

**Tests**:

```bash
python -m pytest tests -q
```

> On Windows, `pip.exe` may be blocked by Application Control — use `python -m pip`.

---

## 3. Environment variables

Put them in `.env` at the repo root. It is gitignored; never commit it.

### Required

| Variable | Purpose |
|---|---|
| `SERPER_API_KEY` | Search — discovery and evidence in both modes |
| `ANTROPIC_API_KEY` *or* `OPENAI_API_KEY` | LLM adjudication (Wikipedia mode only) |

Note the spelling `ANTROPIC_API_KEY` — the code reads that first, then
`ANTHROPIC_API_KEY`. Anthropic is primary; OpenAI is the automatic fallback.
Without any LLM key, Wikipedia mode falls back to Manual Review everywhere.

### Optional

| Variable | Default | Purpose |
|---|---|---|
| `APIFY_TOKEN`, `APIFY_ACTOR_ID` | — | Backup discovery for unresolved platforms |
| `APIFY_INSTAGRAM_ACTOR_ID` | — | Instagram bio reader (Phase 0d) |
| `DATABASE_URL` | — | Postgres: auth, analyst decisions, history |
| `GOOGLE_CLIENT_ID`, `GOOGLE_ALLOWED_DOMAINS` | — | Google sign-in |
| `OPENAI_CHAT_MODEL` | `gpt-4o-mini` | |
| `ANTHROPIC_MODEL` | `claude-opus-4-8` | |
| `PIPELINE_ROW_WORKERS` | `4` | Rows processed concurrently |
| `SERPER_CANDIDATES_PER_PLATFORM` | `4` | Candidates fetched per platform |
| `EXPORT_DIR` | `exports/` | Where workbooks are written |
| `MAX_ROWS_PER_JOB` | `5000` | Upper bound on one run |
| `AMBIGUITY_GUARD` | on | Set `0` to disable the same-name guard |
| `THIN_EVIDENCE_MIN_CONFIDENCE` | `85` | Bar for confirming without a Wikipedia record |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | — | Emails reset codes. Without these the flow degrades, never crashes |
| `APP_BASE_URL` | — | Sign-in link included in emails |
| `RESET_CODE_TTL_MINUTES` | `10` | How long a reset code lives |
| `RESET_MAX_ATTEMPTS` | `5` | Wrong guesses before a code is burned |

### Production only

| Variable | Purpose |
|---|---|
| `APP_ENV=production` | **The important one.** Makes the API fail *closed* |
| `CORS_ORIGINS` | Exact frontend origin(s), comma-separated, no trailing slash |
| `AUTH_REQUIRED` | Defaults to `1`. Never set `0` in production |

`APP_ENV=production` changes four behaviours at once: an unreachable database
means requests are **refused (503)** rather than served anonymously, a
misconfigured container **refuses to start**, `localhost` is dropped as a CORS
origin, and Postgres connections are forced to `sslmode=require`.

---

## 4. Input format

Any `.xlsx` / `.xls` / `.csv`. Headers are detected rather than fixed:

- **Name** — `Talent Name`, `Title`, or the first column
- **Wikipedia URL** — any header containing "wiki", or detected from cell contents
- **Existing handles** — `instagram_user`, `facebook_page`, `twitter_handle`,
  `tiktok_user`, `youtube_channel_username`
- **Profession** — `profession`, `occupation`, `title_sub_category`,
  `title_category`, `genre`, `role` (used to disambiguate custom-mode searches)
- Every other column becomes identity metadata for the verifier

---

## 5. Accounts and sign-in

**There is no public sign-up.** An admin creates every account from
**Users** in the sidebar; the API has no registration endpoint at all, and a
test fails the build if one appears. An internal tool with open registration is
an open door to anyone who finds the URL, and "we'll lock it down later" never
happens.

### Creating an account

Users → *Create an account* → email + role. The server generates a temporary
password and shows it **once** — it is stored only as an Argon2id hash, so
nobody, including the admin, can read it back. Lost it? *Reset password* issues
a new one.

The holder signs in with that temporary password and is sent straight to
*Choose your password* with no way past it. That is what stops a temporary
password quietly becoming a permanent one that two people know.

### Forgotten passwords

Self-service, from the sign-in page: request a **6-digit code by email**, then
enter it with a new password. Six digits is only a million possibilities, so
three rules do the real work — the code **expires in 10 minutes**, dies after
**5 wrong attempts**, and is **deleted on use** so it cannot be replayed. Only
its SHA-256 is stored.

Without SMTP configured the flow still works: in development the code is
printed to the server log (never in production), and an admin can always issue
a fresh temporary password instead.

### The first account

Chicken and egg — only an admin can create accounts, so the first one is made
on the command line:

```bash
python scripts/create_user.py --admin
```

It prompts for the email and password (with `getpass`, so the password never
reaches your shell history) and checks the migrations are in place first. With
no admin in the database yet it defaults to `admin` even without the flag —
an analyst could not create anyone else, so the tool would have no way to grow.

Everyone after that is created in the app.

### Roles

`analyst` runs the pipeline and records decisions. `admin` also manages
accounts. The API enforces this — hiding the sidebar link is a courtesy, and a
`curl` with an analyst's token gets `403`.

---

## 6. Code layout

Grouped by responsibility, so new work has an obvious home.

| Folder | Holds | Where new work goes |
|---|---|---|
| `app/main.py` | FastAPI app: jobs, cancellation, results, decisions, auth | new endpoints |
| `app/pipeline/` | run orchestrator + per-run search options | flow/routing changes |
| `app/discovery/` | finding candidate links (Serper, Apify, bio links, aggregators) | **new discovery source** |
| `app/identity/` | Wikipedia/Wikidata ground truth | identity-data changes |
| `app/verification/` | LLM adjudication + deterministic guards | **new guards** |
| `app/platforms/` | the 5 platforms + URL rules (single source of truth) | **new platform** |
| `app/output/` | input parsing + workbook generation | **new output format** |
| `app/persistence/` | optional Postgres (decisions, auth, history) | schema/storage |
| `app/common/` | shared HTTP retry/session | cross-cutting utils |
| `scripts/` | admin CLI (`create_user.py`) | admin scripts |
| `sql/` | schema, run in numeric order | |
| `tests/` | 197 tests, no network | |
| `curator-ai/` | Next.js 16 UI — discovery, live progress, results, analysis | |

**Start at `app/pipeline/orchestrator.py`** — it routes the two workflows and
every phase hangs off it.

Key modules:

| File | Responsibility |
|---|---|
| `pipeline/orchestrator.py` | The orchestrator. Row routing, both workflows, all phases |
| `pipeline/options.py` | Run config: `mode`, `prompt`, `include_profession` |
| `verification/verifier.py` | LLM verifier (Anthropic → OpenAI), prompt, guards |
| `discovery/serper.py` | `<name> site:<domain>` search + context extraction |
| `discovery/bio_links.py` | Phase 0 first-party bio-link harvesting |
| `discovery/aggregators.py` | Follows Linktree/Beacons/bio.link one hop |
| `discovery/apify.py` | Backup discovery + the Instagram bio reader |
| `identity/wikipedia.py` | Structured ground truth (no LLM, no full article) |
| `platforms/social_urls.py` | The 5 platforms + URL validation/normalisation |
| `output/excel.py` | Input parsing, output schema, workbook writing |

Frontend:

| File | Responsibility |
|---|---|
| `app/discovery/page.tsx` | Upload page |
| `components/SearchModeCard.tsx` | With/Without Wikipedia toggle + prompt |
| `components/DiscoveryWorkspace.tsx` | Upload widget; sends file + search config |
| `lib/search-mode.ts` | Stores mode/prompt/include-profession (localStorage) |

---

## 7. Guards that can only downgrade

The LLM never has the last word on a `Verified`. Each guard can move a verdict to
Manual Review, **never the reverse** — so a guard misfiring costs coverage but
never precision.

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

One guard runs the other way: with a name-only record, a `Wrong` verdict on a
name-matching profile is downgraded to Manual Review. We are not entitled to
assert "this belongs to someone else" from a name and a category alone.

---

## 8. Deployment

- **Frontend** — AWS Amplify, static export (`output: "export"`). The Amplify app
  platform must be `WEB`, not `WEB_COMPUTE`: Amplify's compute service supports
  Next.js 12–15 and this app is on 16.
- **Backend** — container image (`Dockerfile`) on Amazon ECS Express Mode.
- **Database** — RDS PostgreSQL.

`NEXT_PUBLIC_PYTHON_API_URL` is compiled into the frontend bundle at build time,
so changing it requires a redeploy, not just a save.

Full runbook, including the outbound-scraping caveat on AWS IPs, is in
`docs/DEPLOYMENT.md` (kept locally; `docs/` is gitignored).

---

## 9. Known limitations

- **Instagram, TikTok and X expose almost no metadata** to an anonymous fetch, so
  those columns carry more Manual Reviews. The Apify Instagram reader covers the
  Instagram case at a cost per read.
- **Without-Wikipedia mode does not verify.** Anything Serper finds is a candidate
  for a human, by design — there is no ground truth to check it against.
- **Datacenter IPs get blocked harder.** Running on AWS, profile fetches are
  refused more often than from an office IP. It degrades safely (more Manual
  Review, not wrong answers), but measure it before promising numbers.
- **Accuracy is not formally measured.** There is no labelled evaluation set; the
  analyst decisions captured in `verified_url` / `rejected_url` are intended to
  become one. `tests/test_label_decisions.py` pins the label logic against real
  cells in the meantime.
