"""
profile_metadata.py  —  Public profile-metadata enrichment for candidate URLs
==============================================================================

Before the LLM verifies a candidate, we fetch the candidate profile's public
metadata so the model reasons over *evidence*, not just a bare URL:

    username, display name, bio, verified badge, external website,
    follower / following counts, profile image.

Extraction is best-effort and non-authenticated: we read OpenGraph / Twitter
meta tags (and a couple of inline JSON signals) from the public page HTML. If a
page can't be fetched or parsed, the candidate keeps whatever metadata it
already had — accuracy is never reduced, only augmented.

Results are cached per URL for the duration of a run.
"""

from __future__ import annotations

import html as _html
import re
from typing import Any, Dict, List

import requests

import social_urls

# Social sites expose the richest OpenGraph tags to crawler/OG-scraper agents.
# We try the Facebook OG fetcher UA first (unlocks Instagram etc.), then fall
# back to a normal browser UA.
_USER_AGENTS = [
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
]
_BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_TIMEOUT = 10
_MAX_BYTES = 2_500_000

# Instagram/TikTok og:description often leads with follower stats rather than a
# real bio (e.g. "674M Followers, 647 Following, 4,107 Posts - See Instagram …").
_STATS_LEAD_RE = re.compile(r"^\s*[\d][\d.,]*\s*[KMB]?\s+Followers", re.I)

_CACHE: Dict[str, Dict[str, Any]] = {}

# Boilerplate to strip from og:title to recover a clean display name.
_TITLE_NOISE = re.compile(
    r"\s*[\|•\-–—/]\s*(instagram|tiktok|facebook|youtube|x|twitter).*$",
    re.I,
)
_HANDLE_IN_TEXT = re.compile(r"\(@([A-Za-z0-9_.]+)\)")
_COUNT_RE = r"([\d][\d.,]*\s*[KMB]?)"


def clear_cache() -> None:
    _CACHE.clear()


def _fetch_html(url: str) -> str:
    """Fetch page HTML, trying each UA until OpenGraph tags appear."""
    last = ""
    for ua in _USER_AGENTS:
        try:
            resp = requests.get(url, headers={**_BASE_HEADERS, "User-Agent": ua},
                                timeout=_TIMEOUT, allow_redirects=True)
            if not resp.ok or len(resp.content) > _MAX_BYTES:
                continue
            html = resp.text or ""
            last = html
            lowered = html.lower()
            if "og:title" in lowered or "og:description" in lowered:
                return html
        except Exception as exc:  # noqa: BLE001 — enrichment must never raise
            print(f"  [PROFILE] fetch failed {url[:70]}… : {exc.__class__.__name__}")
    return last


def _extract_meta_tags(html: str) -> Dict[str, str]:
    """Collect og:* / twitter:* / name=description meta tags (either attr order)."""
    tags: Dict[str, str] = {}
    for m in re.finditer(r"<meta\b[^>]*>", html, re.I):
        tag = m.group(0)
        key = re.search(r'(?:property|name)\s*=\s*["\']([^"\']+)["\']', tag, re.I)
        content = re.search(r'content\s*=\s*["\']([^"\']*)["\']', tag, re.I)
        if key and content:
            tags[key.group(1).lower()] = content.group(1).strip()
    return tags


def _first_count(text: str, label: str) -> str:
    m = re.search(_COUNT_RE + rf"\s+{label}", text, re.I)
    return m.group(1).strip() if m else ""


def _external_website(description: str, platform: str) -> str:
    for m in re.finditer(r"https?://[^\s\"'<>)\]]+", description):
        url = m.group(0).rstrip(".,);")
        if social_urls.platform_from_url(url) != platform:
            return url
    return ""


def _clean_display_name(og_title: str) -> str:
    name = _HANDLE_IN_TEXT.sub("", og_title)
    name = _TITLE_NOISE.sub("", name)
    return re.sub(r"\s{2,}", " ", name).strip(" -–—|•")


def fetch_profile_metadata(url: str, platform: str) -> Dict[str, Any]:
    """Return best-effort public metadata for a profile URL (cached per URL)."""
    if url in _CACHE:
        return _CACHE[url]

    meta: Dict[str, Any] = {"username": social_urls.handle_from_url(url, platform)}
    html = _fetch_html(url)
    if not html:
        _CACHE[url] = meta
        return meta

    tags = _extract_meta_tags(html)
    title = _html.unescape(tags.get("og:title") or tags.get("twitter:title") or "")
    description = _html.unescape(
        tags.get("og:description") or tags.get("twitter:description") or ""
    )
    image = tags.get("og:image") or ""

    if title:
        display = _clean_display_name(title)
        if display:
            meta["display_name"] = display
        handle_match = _HANDLE_IN_TEXT.search(title)
        if handle_match and not meta.get("username"):
            meta["username"] = handle_match.group(1)
    if description:
        for field, label in (("followers", "Followers"), ("following", "Following"),
                             ("likes", "Likes")):
            value = _first_count(description, label)
            if value:
                meta[field] = value
        # Only treat the description as a bio when it isn't just a stats summary.
        if not _STATS_LEAD_RE.match(description):
            meta["bio"] = description[:400]
        website = _external_website(description, platform)
        if website:
            meta["website"] = website
    if image:
        meta["image"] = image

    # Verified badge — only set when the page exposes a clear signal.
    if re.search(r'"is_verified"\s*:\s*true|"isVerified"\s*:\s*true|"verified"\s*:\s*true',
                 html, re.I):
        meta["verified"] = True

    # Drop empty values so the LLM payload stays clean.
    meta = {k: v for k, v in meta.items() if v not in ("", None)}
    _CACHE[url] = meta
    return meta


def enrich_candidates(candidates: List[dict], platform: str) -> None:
    """
    Attach fetched profile metadata to each candidate's ``meta`` dict, in place.
    Existing metadata (e.g. from Apify) is preserved; fetched fields only fill
    gaps. Candidates already enriched this run are skipped.
    """
    for cand in candidates:
        if cand.get("_enriched"):
            continue
        url = cand.get("url", "")
        if not url:
            continue
        fetched = fetch_profile_metadata(url, platform)
        merged = dict(fetched)
        merged.update({k: v for k, v in (cand.get("meta") or {}).items() if v not in ("", None)})
        cand["meta"] = merged
        cand["_enriched"] = True
