"""
serper_service.py  —  Serper.dev search (fallback candidate discovery)
======================================================================

Used ONLY when Apify (and Wikidata) did not supply a link for a platform.
For a missing platform we issue an "<Name> Official <Platform>" style query
and return the top few profile-URL candidates for the LLM to rank.

Environment variables:
    SERPER_API_KEY   Serper.dev API key (required for fallback search)

The low-level :func:`serper_search` is the single canonical Serper call for
the whole project.
"""

from __future__ import annotations

import os
import time
from typing import Dict, List

import requests

import social_urls

SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "").strip()

_SERPER_URL = "https://google.serper.dev/search"

# Extra query hint per platform to bias results toward the official profile.
_PLATFORM_QUERY_HINT: Dict[str, str] = {
    "Instagram": "official Instagram",
    "X": "official X Twitter",
    "Facebook": "official Facebook",
    "YouTube": "official YouTube channel",
    "TikTok": "official TikTok",
}


def is_configured() -> bool:
    return bool(SERPER_API_KEY)


def serper_search(query: str, num_results: int = 10) -> List[dict]:
    """Canonical Serper organic search. Raises RuntimeError on fatal API errors."""
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload = {"q": query, "num": max(1, min(num_results, 10))}
    response = requests.post(_SERPER_URL, headers=headers, json=payload, timeout=30)
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        try:
            detail = response.json().get("message") or response.text
        except ValueError:
            detail = response.text
        raise RuntimeError(f"Serper search failed ({response.status_code}): {detail}") from exc
    data = response.json()
    return [
        {
            "title": item.get("title", "") or "",
            "snippet": item.get("snippet", "") or "",
            "link": item.get("link", "") or "",
        }
        for item in data.get("organic", [])
    ]


def search_platform_candidates(
    talent: str,
    platform: str,
    top_n: int = 4,
) -> List[dict]:
    """
    Return up to ``top_n`` profile-URL candidates for a talent on a platform.

    Candidates are shaped like ``{url, source, title, snippet}`` for the LLM.
    Returns ``[]`` on any failure so the run continues.
    """
    if not is_configured():
        print("  [SERPER] Skipped — SERPER_API_KEY not set.")
        return []

    domain = social_urls.PLATFORMS.get(platform, [""])[0]
    hint = _PLATFORM_QUERY_HINT.get(platform, f"official {platform}")
    queries = [
        f'{talent} {hint}',
        f'site:{domain} "{talent}"',
    ]

    candidates: List[dict] = []
    seen: set = set()
    for query in queries:
        try:
            results = serper_search(query, num_results=10)
        except RuntimeError as exc:
            # Fatal API error (quota/auth) — surface so the caller can decide.
            print(f"  [SERPER] Fatal error on '{query}': {exc}")
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"  [SERPER] Query failed '{query}': {exc}")
            continue

        for item in results:
            link = item.get("link", "")
            if social_urls.platform_from_url(link) != platform:
                continue
            if not social_urls.is_valid_profile_url(link, platform):
                continue
            norm = social_urls.normalize_profile_url(link, platform)
            if norm in seen:
                continue
            seen.add(norm)
            candidates.append({
                "url": norm,
                "source": "serper",
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
            })
            if len(candidates) >= top_n:
                break
        if len(candidates) >= top_n:
            break
        time.sleep(0.2)

    print(f"  [SERPER] {platform} | '{talent}' -> {len(candidates)} candidate(s)")
    return candidates
