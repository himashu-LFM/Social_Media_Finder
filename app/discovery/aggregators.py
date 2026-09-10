"""
aggregators.py — follow link-aggregator pages (Linktree etc.) to recover the
other-platform profile links they list.

Why this exists: a client-supplied Instagram bio rarely lists the person's
YouTube/TikTok/X handles directly. It lists a **Linktree** (or Beacons, bio.link,
komi.io, …) that lists them. Reading the anchor page therefore surfaces the
aggregator URL but not the handles; this module fetches that aggregator page and
extracts the platform profile links off it, reusing the exact same classifier
``bio_links`` uses on any page.

Scope, deliberately narrow — mirrors ``bio_links``:
  * It only **finds** links; identity is decided elsewhere.
  * Best-effort and fail-soft: a blocked or empty aggregator returns ``{}`` and
    the caller carries on. It never raises.
  * No login, no anti-bot evasion. Aggregator pages are public, so a plain GET
    is enough (verified against real linktr.ee pages).

``bio_links`` is imported lazily inside the functions so that ``bio_links`` can
import this module at its top level without a circular import.
"""

from __future__ import annotations

import threading
from typing import Dict, List
from urllib.parse import urlparse

from app.output import profile_metadata

# Known link-aggregator hosts. A link to one of these in a bio is a pointer to a
# page that lists the subject's real platform profiles. Extend as new ones show
# up in real files — each is just another host; the extractor handles the rest.
AGGREGATOR_HOSTS = frozenset({
    "linktr.ee", "beacons.ai", "bio.link", "lnk.bio", "komi.io", "solo.to",
    "campsite.bio", "linkin.bio", "tap.bio", "allmylinks.com", "hoo.be",
    "msha.ke", "many.link", "carrd.co", "withkoji.com", "snipfeed.co",
})

_CACHE: Dict[str, Dict[str, str]] = {}
_LOCK = threading.Lock()


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def _host(url: str) -> str:
    try:
        host = (urlparse(url).netloc or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def is_aggregator(url: str) -> bool:
    """True if ``url`` points at a known link-aggregator page (host match only,
    so ``example.com/linktr.ee`` is correctly NOT treated as one)."""
    host = _host(url)
    return any(host == h or host.endswith("." + h) for h in AGGREGATOR_HOSTS)


def aggregator_urls_in(html_text: str) -> List[str]:
    """Every aggregator URL that appears on a page, de-duplicated, document order."""
    from app.discovery import bio_links  # lazy — avoids a circular import
    seen: set = set()
    out: List[str] = []
    for url in bio_links._candidate_urls(html_text):
        if is_aggregator(url) and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def follow(aggregator_url: str, exclude_platform: str = "",
           exclude_url: str = "") -> Dict[str, str]:
    """
    Read an aggregator page and return ``{platform: profile_url}`` for the
    platform profiles it lists. Returns ``{}`` for a non-aggregator URL, an
    unreachable/empty page, or any error — following must never raise.
    """
    if not aggregator_url or not is_aggregator(aggregator_url):
        return {}

    with _LOCK:
        if aggregator_url in _CACHE:
            return dict(_CACHE[aggregator_url])

    from app.discovery import bio_links  # lazy — avoids a circular import
    found: Dict[str, str] = {}
    try:
        html_text = profile_metadata._fetch_html(aggregator_url)
    except Exception as exc:  # noqa: BLE001 — following must never raise
        print(f"  [AGGREGATOR] fetch failed {aggregator_url[:60]}… : "
              f"{exc.__class__.__name__}")
        html_text = ""
    if html_text:
        found = bio_links.links_by_platform(
            html_text, exclude_platform=exclude_platform, exclude_url=exclude_url
        )

    if found:
        print(f"  [AGGREGATOR] {aggregator_url[:56]}… -> {', '.join(sorted(found))}")

    with _LOCK:
        _CACHE[aggregator_url] = dict(found)
    return found
