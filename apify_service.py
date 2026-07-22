"""
apify_service.py  —  Apify "Social Media Finder" integration
============================================================

Calls a configurable Apify actor to discover social-profile links for a
talent, then normalises the actor's output into per-platform candidate links.

The actor is intentionally NOT hard-coded: different Apify social-finder
actors return different JSON shapes, so this module is *schema-agnostic*. It
runs the actor synchronously, then scans every returned dataset item for
recognised social-profile URLs (via :mod:`social_urls`) and, when possible,
attaches the item's own profile metadata (username, display name, bio,
verified flag, follower count) to the candidate.

Environment variables (set the real values in .env):
    APIFY_TOKEN            Apify API token (required to call the actor)
    APIFY_ACTOR_ID         Actor id or "user~actor-name" slug (required)
    APIFY_ACTOR_INPUT      Optional JSON input template. May contain the
                           placeholders {name} and {website}. Defaults to
                           '{"name": "{name}"}'.
    APIFY_TIMEOUT_SECONDS  Sync-run timeout (default 120)

Returns per talent:
    {platform: [ {url, source, meta{...}}, ... ]}
Only platforms that actually resolve to a profile URL appear — missing
platforms are simply absent (never forced).
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

import requests

import social_urls
from retry_util import request_with_retry

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "").strip()
APIFY_ACTOR_ID = os.environ.get("APIFY_ACTOR_ID", "").strip()
APIFY_ACTOR_INPUT = os.environ.get("APIFY_ACTOR_INPUT", "").strip()
APIFY_TIMEOUT_SECONDS = int(os.environ.get("APIFY_TIMEOUT_SECONDS", "120"))
# Batch settings: how many names per actor run, and how many runs in parallel.
# The actor rejects/times out on large runs, so we keep chunks small and get
# throughput from running several chunks concurrently. A chunk that still fails
# is auto-split (see _run_chunk), so a bad name never wipes out a whole batch.
APIFY_CHUNK_SIZE = max(1, int(os.environ.get("APIFY_CHUNK_SIZE", "5")))
APIFY_CHUNK_WORKERS = max(1, int(os.environ.get("APIFY_CHUNK_WORKERS", "4")))

_APIFY_BASE = "https://api.apify.com/v2"

# Socials this actor supports (matches the tri_angle/social-media-finder enum).
_DEFAULT_SOCIALS = ["instagram", "facebook", "tiktok", "youtube"]

# Case-insensitive key hints used to pull profile metadata off a dataset item.
_META_KEYS: Dict[str, List[str]] = {
    "username": ["username", "handle", "userName", "screenName", "screen_name"],
    "display_name": ["fullName", "name", "displayName", "title", "full_name"],
    "bio": ["biography", "bio", "description", "about"],
    "verified": ["verified", "isVerified", "is_verified"],
    "followers": ["followersCount", "followers", "followers_count", "subscriberCount"],
}


def is_configured() -> bool:
    """True when both the token and actor id are present in the environment."""
    return bool(APIFY_TOKEN and APIFY_ACTOR_ID)


def _configured_socials() -> List[str]:
    """Socials list from the env template if the user set one, else the default."""
    if APIFY_ACTOR_INPUT:
        try:
            tpl = json.loads(APIFY_ACTOR_INPUT.replace("{name}", "x").replace("{website}", ""))
            socials = tpl.get("socials")
            if isinstance(socials, list) and socials:
                return socials
        except json.JSONDecodeError:
            pass
    return list(_DEFAULT_SOCIALS)


def _actor_input(names: List[str]) -> dict:
    """
    Build the actor input for one or many names. Built as a real dict (then
    JSON-encoded by requests), so names containing quotes/backslashes can never
    corrupt the payload — that previously caused Apify to return nothing.
    """
    return {"profileNames": list(names), "socials": _configured_socials()}


def _run_actor(actor_input: dict) -> List[dict]:
    """Run the actor synchronously and return its dataset items (list of dicts)."""
    url = f"{_APIFY_BASE}/acts/{APIFY_ACTOR_ID}/run-sync-get-dataset-items"
    params = {"token": APIFY_TOKEN, "timeout": APIFY_TIMEOUT_SECONDS}
    resp = request_with_retry(
        "POST", url, params=params, json=actor_input,
        timeout=APIFY_TIMEOUT_SECONDS + 15,
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        # Some actors wrap items under a key; fall back to a single-item list.
        for key in ("items", "results", "data"):
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    return data if isinstance(data, list) else []


def _get_meta(item: dict) -> Dict[str, Any]:
    """Pull recognised profile-metadata fields off a dataset item."""
    lowered = {str(k).lower(): v for k, v in item.items()}
    meta: Dict[str, Any] = {}
    for out_key, candidates in _META_KEYS.items():
        for cand in candidates:
            if cand.lower() in lowered and lowered[cand.lower()] not in (None, ""):
                meta[out_key] = lowered[cand.lower()]
                break
    return meta


def _iter_strings(node: Any):
    """Yield every string value found anywhere in a nested JSON structure."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _iter_strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_strings(value)


def _collect_candidates(items: List[dict]) -> Dict[str, List[dict]]:
    """Scan actor output for profile URLs and group them by platform."""
    by_platform: Dict[str, List[dict]] = {}
    seen: Dict[str, set] = {}

    def add(url: str, meta: Dict[str, Any]) -> None:
        platform = social_urls.platform_from_url(url)
        if not platform or not social_urls.is_valid_profile_url(url, platform):
            return
        norm = social_urls.normalize_profile_url(url, platform)
        bucket = seen.setdefault(platform, set())
        if norm in bucket:
            return
        bucket.add(norm)
        by_platform.setdefault(platform, []).append(
            {"url": norm, "source": "apify", "meta": meta}
        )

    for item in items:
        item_meta = _get_meta(item) if isinstance(item, dict) else {}
        for value in _iter_strings(item):
            if value.startswith("http"):
                add(value, item_meta)

    return by_platform


def _item_name(item: dict) -> str:
    """The profile name the actor echoes back for a result row."""
    if not isinstance(item, dict):
        return ""
    for key in ("inputProfileName", "profileName", "query", "input"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _run_chunk(chunk: List[str]) -> List[dict]:
    """
    Run one actor call for a chunk of names. If it fails (the actor 400s or
    times out on a too-large / problematic chunk), split the chunk in half and
    retry each half — so the failure self-heals down to per-name calls instead
    of losing the whole chunk. Never raises (returns [] only for a single name
    that genuinely fails).
    """
    try:
        return _run_actor(_actor_input(chunk))
    except (requests.Timeout, requests.HTTPError, requests.ConnectionError) as exc:
        detail = getattr(getattr(exc, "response", None), "status_code", exc.__class__.__name__)
        if len(chunk) > 1:
            mid = len(chunk) // 2
            print(f"  [APIFY] chunk of {len(chunk)} failed ({detail}) — splitting and retrying.")
            return _run_chunk(chunk[:mid]) + _run_chunk(chunk[mid:])
        print(f"  [APIFY] name '{chunk[0]}' failed ({detail}).")
        return []
    except Exception as exc:  # noqa: BLE001 — never let Apify abort the run
        print(f"  [APIFY] Chunk call failed: {exc.__class__.__name__}: {exc}")
        return []


def find_social_links_batch(names: List[str]) -> Dict[str, Dict[str, List[dict]]]:
    """
    Discover social-profile candidates for MANY talents in one batched pass.

    Names are sent to the actor in chunks (APIFY_CHUNK_SIZE) with a few chunks
    running in parallel (APIFY_CHUNK_WORKERS), instead of one actor run per
    talent — this removes most of the per-run overhead. Results are grouped back
    to each talent by the actor's echoed profile name.

    Returns ``{talent_name: {platform: [candidate, ...]}}``.
    """
    clean = [n for n in dict.fromkeys(n.strip() for n in names) if n]
    empty = {n: {} for n in clean}
    if not clean or not is_configured():
        if not is_configured():
            print("  [APIFY] Skipped — APIFY_TOKEN / APIFY_ACTOR_ID not set.")
        return empty

    chunks = [clean[i:i + APIFY_CHUNK_SIZE] for i in range(0, len(clean), APIFY_CHUNK_SIZE)]
    workers = min(APIFY_CHUNK_WORKERS, len(chunks))

    all_items: List[dict] = []
    if workers <= 1:
        for chunk in chunks:
            all_items.extend(_run_chunk(chunk))
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for items in pool.map(_run_chunk, chunks):
                all_items.extend(items)

    # Group items back to each requested talent by the echoed name.
    lookup = {n.lower(): n for n in clean}
    grouped: Dict[str, List[dict]] = {n: [] for n in clean}
    for item in all_items:
        echoed = _item_name(item).lower()
        target = lookup.get(echoed)
        if target:
            grouped[target].append(item)

    results = {n: _collect_candidates(items) for n, items in grouped.items()}
    total = sum(len(c) for r in results.values() for c in r.values())
    print(f"  [APIFY] Batch: {len(clean)} name(s) in {len(chunks)} chunk(s) -> "
          f"{total} candidate link(s)")
    return results


def find_social_links(talent: str, website: str = "") -> Dict[str, List[dict]]:
    """Single-talent convenience wrapper around :func:`find_social_links_batch`."""
    return find_social_links_batch([talent]).get(talent.strip(), {})
