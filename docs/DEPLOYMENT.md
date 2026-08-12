# Deploying Curator AI on AWS

Two things this document is for: making sure nobody reaches the tool who
shouldn't, and making sure nobody who should is blocked. There is also one
problem that configuration cannot fix — see **Outbound scraping** — and it is
the most likely thing to actually break in production, so read that section
even if you skip the rest.

---

## 1. The single most important setting

```
APP_ENV=production
```

Without it the API runs in development mode, and development mode is designed to
be forgiving. Setting it turns on four behaviours at once:

| Behaviour | Development | Production (`APP_ENV=production`) |
|---|---|---|
| Database unreachable | API serves everyone, unauthenticated | Every request refused with **503** |
| Misconfigured at boot | Warning in the log | **Container refuses to start** |
| `localhost` CORS origin | Allowed | Rejected |
| Postgres connection | Whatever libpq negotiates | `sslmode=require` forced |

The first row is the one that matters. `enforced()` returns false when the
account store cannot be read, and that used to mean *open to the world*. A
missing `DATABASE_URL`, an RDS failover, a security-group change — any of them
would silently have removed authentication from every endpoint. In production
the API now refuses what it cannot authenticate.

---

## 2. Environment variables

### Required

| Variable | Value | If missing |
|---|---|---|
| `APP_ENV` | `production` | Silently runs in permissive dev mode |
| `DATABASE_URL` | `postgresql://user:pass@<rds-endpoint>:5432/curator` | **Container will not start** |
| `CORS_ORIGINS` | `https://curator.yourdomain.com` — exact scheme and host, comma-separated, no trailing slash | **Container will not start**; the UI could not call the API anyway |
| `SERPER_API_KEY` | | Discovery finds nothing |
| `ANTROPIC_API_KEY` *or* `OPENAI_API_KEY` | | Everything becomes Manual Review |

### Recommended

| Variable | Default | Notes |
|---|---|---|
| `SESSION_TTL_HOURS` | `12` | How long a sign-in lasts |
| `GOOGLE_CLIENT_ID` | — | Enables the Google button; without it the page is password-only |
| `GOOGLE_ALLOWED_DOMAINS` | — | e.g. `listenfirstmedia.com`. **Empty means only pre-existing accounts can sign in** — the safe default. An OAuth client alone would otherwise admit any Google account on earth |
| `MAX_ROWS_PER_JOB` | `5000` | Upper bound on one run |
| `EXPORT_DIR` | `./exports` | Point at a mounted volume if you have one |

### Never set in production

`AUTH_REQUIRED=0` disables authentication entirely. The startup check refuses to
boot if it is set alongside `APP_ENV=production`, but do not put it in a task
definition in the first place.

### Where secrets go

AWS Secrets Manager or SSM Parameter Store, referenced from the task definition
— not in the image, not in `.env`, not in the repo. `.env` is gitignored and
untracked (verified), and the log formatter redacts Authorization headers,
cookies and tokens. Passwords are Argon2id hashes; session tokens are stored
only as SHA-256, so a database dump yields neither a password nor a usable
session.

---

## 3. What is public on purpose

Five routes, and a test (`test_no_route_is_accidentally_public`) fails the build
if a sixth appears:

| Route | Why |
|---|---|
| `GET /api/health` | Load-balancer probe. Returns a fixed string, reveals nothing |
| `GET /api/auth/status` | Tells the sign-in page whether to show the Google button |
| `POST /api/auth/login` | How you obtain a session |
| `POST /api/auth/google` | Same, via Google. The ID token is verified against Google's public keys server-side; the email a client claims is never trusted |
| `POST /api/auth/logout` | Revokes a token; harmless without one |

Everything else — uploads, jobs, results, exports, decisions, history, and
`/api/db/health` — requires a bearer token.

`POST /api/upload` and `POST /api/jobs/upload` are two decorators on one
function, so both are authenticated. This was verified rather than assumed.

---

## 4. Running more than one container

The API keeps live job progress in memory. That is fine for one instance and
wrong for several, so:

**Option A — one instance (recommended to start).** App Runner or ECS with
`desired_count = 1`. Simplest, and sufficient for an analyst team.

**Option B — several instances.** Two things must be true:

1. **Sticky sessions on the load balancer**, so a browser polling
   `GET /api/jobs/{id}` reaches the instance running that job. Without it,
   progress appears to freeze at random. The endpoint does fall back to
   Postgres, so results are never lost — only live progress stutters.
2. Nothing depends on local disk. Uploads are deleted after parsing, and
   `/api/export/latest` rebuilds the workbook from the job's rows in Postgres
   when the file is not on the local filesystem.

Either way, run the migrations first, in order:

```bash
psql "$DATABASE_URL" -f sql/001_create_url_tables.sql
psql "$DATABASE_URL" -f sql/002_create_job_tables.sql
psql "$DATABASE_URL" -f sql/003_create_auth_and_history.sql
```

Then create the first account — interactively, so the password never enters
shell history or a log:

```bash
python create_user.py
```

`reap_orphaned_jobs()` runs at startup and marks jobs that a previous container
left mid-flight as failed, so a deploy does not leave rows stuck at "running"
for ever.

---

## 5. Outbound scraping — read this one

**This is the most likely thing to break, and no configuration fixes it.**

The pipeline reads public profile pages directly (`profile_metadata`,
`bio_link_service`). Those requests currently come from a home/office IP. From
AWS they will come from a well-known datacenter range, and Instagram, TikTok and
X block those far more aggressively than residential addresses.

What that costs, concretely: profile fetches start returning nothing, candidates
look evidence-free, and the evidence gate escalates them. **The failure mode is
a flood of Manual Review, not wrong answers** — the guards are one-directional,
so precision is preserved and only coverage drops. That is the right way for
this to fail, but it will not be subtle.

Measure it before you promise anything to the client:

```bash
# From an EC2 box in the target region/VPC — not from a laptop.
python - <<'EOF'
from dotenv import load_dotenv; load_dotenv()
import profile_metadata as pm
for url, platform in [("https://www.instagram.com/mrbeast", "Instagram"),
                      ("https://www.youtube.com/@MrBeast", "YouTube"),
                      ("https://www.tiktok.com/@mrbeast", "TikTok")]:
    got = pm.fetch_profile_metadata(url, platform)
    print(f"{platform:10s} {'OK  ' if got.get('display_name') else 'BLOCKED'} {got}")
EOF
```

If platforms come back blocked, in increasing order of effort:

1. **Lean on Apify** for those platforms. It already runs as the backup phase
   and does its own fetching, so it is unaffected by our egress IP.
2. **NAT Gateway with an Elastic IP**, then watch whether that single address
   gets rate-limited — it will be, at volume.
3. **A residential/rotating proxy** for the scrape path only. This is a
   commercial and policy decision, not a technical one: it needs sign-off from
   whoever owns terms-of-service risk, and it should never be used to bypass a
   block that a platform has applied deliberately to us.

Note the asymmetry we measured: **YouTube reads reliably; Instagram already
returns nothing useful even from a residential IP.** So the loss from moving to
AWS is smaller than it looks — Instagram is already contributing nothing to the
scrape path, and Apify is what covers it.

---

## 6. Pre-deploy checklist

```bash
python -m pytest tests -q      # 141 tests; test_deployment_safety.py covers this document
cd curator-ai && npm run build
```

Then confirm, in the deployed environment:

- [ ] `APP_ENV=production` is set
- [ ] The container **started** — if it exited, read the `FATAL CONFIG:` lines; they name the exact variable
- [ ] `GET /api/health` returns 200 through the load balancer
- [ ] `GET /api/results/latest` **without** a token returns **401**, not data
- [ ] Signing in from the real UI origin works (proves `CORS_ORIGINS` is right)
- [ ] The RDS security group admits only the app's security group, not `0.0.0.0/0`
- [ ] HTTPS everywhere — the bearer token is in a header, and plain HTTP would expose it
- [ ] Run the scraping check in section 5 from inside the VPC
- [ ] Rotate any credential that has ever appeared in a terminal or a chat log
