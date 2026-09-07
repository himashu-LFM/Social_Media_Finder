# Architecture

Backend package layout under `app/`. Group by **responsibility** so new features
have an obvious home and the flat root stops growing.

## Folder responsibilities

| Folder | Holds | Where new work goes |
|---|---|---|
| `app/api/` | FastAPI app, routes, pydantic schemas | new endpoints |
| `app/pipeline/` | run orchestrator + per-run search options | flow/routing changes |
| `app/discovery/` | finding candidate links (Serper, SerpApi, Apify, bio links, aggregators) | **new discovery source** |
| `app/identity/` | Wikipedia/Wikidata ground truth | identity-data changes |
| `app/verification/` | LLM adjudication + deterministic guards | new guards |
| `app/platforms/` | the 5 platforms + URL rules (single source of truth) | **new platform** |
| `app/output/` | input parsing + workbook generation | **new output format** |
| `app/persistence/` | optional Postgres (decisions, auth, history) | schema/storage |
| `app/common/` | shared HTTP retry/session + helpers | cross-cutting utils |
| `scripts/` | one-off CLI utilities (e.g. create_user) | admin scripts |

## Migration map (Phase 2 — current file → destination)

| Current (flat root) | → Destination |
|---|---|
| `api_server.py` | `app/main.py` (+ later split into `app/api/routes_*.py`, `app/api/schemas.py`) |
| `verification_pipeline.py` | `app/pipeline/orchestrator.py` |
| `search_options.py` | `app/pipeline/options.py` |
| `serper_service.py` | `app/discovery/serper.py` |
| `serpapi_service.py` | `app/discovery/serpapi.py` |
| `apify_service.py` | `app/discovery/apify.py` |
| `bio_link_service.py` | `app/discovery/bio_links.py` |
| _(future)_ Linktree follower | `app/discovery/aggregators.py` |
| `wikipedia_service.py` | `app/identity/wikipedia.py` |
| `wikidata_lookup.py` | `app/identity/wikidata.py` |
| `verification_service.py` | `app/verification/verifier.py` |
| `social_urls.py` | `app/platforms/social_urls.py` |
| `excel_service.py` | `app/output/excel.py` |
| `profile_metadata.py` | `app/output/profile_metadata.py` _(or `app/common/` — decide at move time)_ |
| `db_service.py` | `app/persistence/db.py` |
| `auth_service.py` | `app/persistence/auth.py` |
| `retry_util.py` | `app/common/retry.py` |
| `create_user.py` | `scripts/create_user.py` |
| _(future)_ centralized settings | `app/config.py` |

## Migration rules (keep code from being sacrificed)
1. Move **one group at a time** with `git mv` (preserves blame/history).
2. After each move: update all importers → run the **149 tests** → commit.
3. Behavior never changes in this refactor — location only.
4. Entry point changes: `uvicorn api_server:app` → `uvicorn app.main:app`
   (update Dockerfile, `apprunner.yaml`, README, `docs/DEPLOYMENT.md`).
5. Frontend is unaffected (`NEXT_PUBLIC_PYTHON_API_URL` is just a URL).

## Status
- **Phase 0 — skeleton:** done.
- **Phase 2 — moves:** done. All backend modules moved into `app/` (+ `scripts/create_user.py`); entry is `app.main:app`; 149 tests green; server boots.
- Follow-ups (optional): split `app/main.py` into `app/api/routes_*.py`, add `app/config.py`, refresh the README layout section (superseded by this file), drop the `as old_name` import aliases for full cleanliness.
