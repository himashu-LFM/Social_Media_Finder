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
from typing import Any, Dict, List

import requests

import social_urls

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "").strip()
APIFY_ACTOR_ID = os.environ.get("APIFY_ACTOR_ID", "").strip()
APIFY_ACTOR_INPUT = os.environ.get("APIFY_ACTOR_INPUT", "").strip()
APIFY_TIMEOUT_SECONDS = int(os.environ.get("APIFY_TIMEOUT_SECONDS", "120"))

_APIFY_BASE = "https://api.apify.com/v2"

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


def _build_actor_input(talent: str, website: str) -> dict:
    """Render the configured input template (or a sensible default)."""
    template = APIFY_ACTOR_INPUT or '{"name": "{name}"}'
    rendered = template.replace("{name}", talent).replace("{website}", website or "")
    try:
        return json.loads(rendered)
    except json.JSONDecodeError as exc:
        print(f"  [APIFY] Invalid APIFY_ACTOR_INPUT template ({exc}); using default.")
        return {"name": talent}


def _run_actor(actor_input: dict) -> List[dict]:
    """Run the actor synchronously and return its dataset items (list of dicts)."""
    url = f"{_APIFY_BASE}/acts/{APIFY_ACTOR_ID}/run-sync-get-dataset-items"
    params = {"token": APIFY_TOKEN, "timeout": APIFY_TIMEOUT_SECONDS}
    resp = requests.post(
        url,
        params=params,
        json=actor_input,
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


def find_social_links(talent: str, website: str = "") -> Dict[str, List[dict]]:
    """
    Look up social-profile candidates for a talent via Apify.

    Returns ``{platform: [candidate, ...]}``. On any failure (not configured,
    network/timeout error, bad payload) returns ``{}`` so the caller can fall
    back to Serper without the run aborting.
    """
    if not is_configured():
        print("  [APIFY] Skipped — APIFY_TOKEN / APIFY_ACTOR_ID not set.")
        return {}

    actor_input = _build_actor_input(talent, website)
    try:
        items = _run_actor(actor_input)
    except requests.Timeout:
        print(f"  [APIFY] Timeout running actor for '{talent}'.")
        return {}
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        print(f"  [APIFY] Actor HTTP error ({status}) for '{talent}'.")
        return {}
    except Exception as exc:  # noqa: BLE001 — never let Apify abort the row
        print(f"  [APIFY] Actor call failed for '{talent}': {exc.__class__.__name__}: {exc}")
        return {}

    candidates = _collect_candidates(items)
    summary = {p: len(c) for p, c in candidates.items()}
    print(f"  [APIFY] '{talent}' -> {summary or 'no profiles found'}")
    return candidates
