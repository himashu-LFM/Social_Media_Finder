"""
serpapi_service.py  —  SerpApi "Google AI Mode" discovery (no-Wikipedia rows)
=============================================================================

For talents WITHOUT a Wikipedia link, discovery switches from Serper to
SerpApi's Google AI Mode engine, with a different query pattern:

    "<Name> <Profession> all social media handles"
        e.g. "Karan Aujla Singer all social media handles"

Google AI Mode returns an AI-written answer (``reconstructed_markdown`` +
``text_blocks`` + ``references``) that names the person's official handles. We
scan the whole response for valid social-profile URLs, group them by platform,
and attach the AI answer as context so the LLM can tag each one
Verified / Manual Review / Wrong / Not Found against the ground truth.

Environment variables:
    SERPAPI_API_KEY          SerpApi key (required for this flow)
    SERPAPI_MAX_CONCURRENCY  Max concurrent SerpApi calls (default 4)
"""

from __future__ import annotations

import json
import os
import re
import threading
from typing import Any, Dict, List, Optional

import requests

import social_urls
from retry_util import request_with_retry

SERPAPI_API_KEY = os.environ.get("SERPAPI_API_KEY", "").strip()
SERPAPI_ENGINE = os.environ.get("SERPAPI_ENGINE", "google_ai_mode").strip()

_SERPAPI_URL = "https://serpapi.com/search.json"

# Bound concurrent SerpApi calls (its rate limits are stricter than Serper's).
_SERPAPI_MAX_CONCURRENCY = max(1, int(os.environ.get("SERPAPI_MAX_CONCURRENCY", "4")))
_SERPAPI_SEM = threading.BoundedSemaphore(_SERPAPI_MAX_CONCURRENCY)

# Query suffix appended after "<Name> <Profession>".
_QUERY_SUFFIX = os.environ.get("SERPAPI_QUERY_SUFFIX", "social media handles").strip()

# URLs in the AI answer arrive with trailing markdown artifacts — stop at the
# first space/quote/backslash/paren/bracket/angle so we capture the bare URL.
_URL_RE = re.compile(r"https?://[^\s\"'\)\]\\<>]+")

# Host fragments that are never real profiles (Google's favicon/tracking CDN).
_JUNK_HOSTS = ("gstatic.com", "googleusercontent.com", "favicon", "google.com/search")

# Prose fallback: Google AI Mode sometimes names a handle ONLY in the answer text
# ("TikTok: @handle", "@handle on Facebook") with no link element attached. These
# map the platform words it uses in prose to our platform keys so we can rebuild
# the profile URL from the handle. (Bare "x" is intentionally excluded — too many
# false hits; X is caught via "twitter"/"x (twitter)" or its own link.)
_PROSE_PLATFORM_ALIASES: Dict[str, str] = {
    "Instagram": r"instagram",
    "Facebook":  r"facebook",
    "YouTube":   r"youtube",
    "TikTok":    r"tik\s?tok",
    "X":         r"x\s*\(twitter\)|twitter",
}
# A handle must be @-prefixed in prose (so display names like "Brandi Marie King
# YouTube Channel" are never mistaken for a handle) and a clean single token.
_PROSE_HANDLE = r"[A-Za-z0-9._]{2,50}"


def _iter_prose_handles(text: str):
    """
    Yield ``(platform, handle)`` pairs that the AI answer names only in prose,
    with no link element — e.g. ``"TikTok: @lovebrandimarie"`` or
    ``"@lovebrandimarie on Facebook"``. The ``@`` is required, so plain display
    names are ignored.
    """
    if not text:
        return
    for platform, alias in _PROSE_PLATFORM_ALIASES.items():
        # "Platform: @handle" / "Platform - @handle" / "Platform [@handle"
        for m in re.finditer(rf"(?:{alias})[\s:\-]*\[?@({_PROSE_HANDLE})", text, re.I):
            yield platform, m.group(1)
        # "@handle on Platform"
        for m in re.finditer(rf"@({_PROSE_HANDLE})\s+on\s+(?:{alias})", text, re.I):
            yield platform, m.group(1)


def is_configured() -> bool:
    return bool(SERPAPI_API_KEY)


def _clean_url(url: str) -> str:
    """Strip trailing markdown/punctuation left over from the AI answer text."""
    return url.rstrip(").,;:!\\\"'>]}").strip()


def _search(query: str) -> dict:
    params = {"engine": SERPAPI_ENGINE, "q": query, "api_key": SERPAPI_API_KEY}
    with _SERPAPI_SEM:
        resp = request_with_retry("GET", _SERPAPI_URL, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def _iter_snippet_links(node: Any):
    """
    Walk the AI-answer structure and yield (link, text, snippet) from every
    ``snippet_links`` entry — the format Google AI Mode uses to cite a handle:

        {"snippet": "Instagram: @yasashi_mirai",
         "snippet_links": [{"text": "@yasashi_mirai",
                            "link": "https://www.instagram.com/yasashi_mirai/"}]}

    Recurses through nested ``list`` / ``text_blocks`` blocks.
    """
    if isinstance(node, dict):
        snippet = str(node.get("snippet", "") or "")
        for sl in node.get("snippet_links", []) or []:
            if isinstance(sl, dict) and sl.get("link"):
                yield sl["link"], str(sl.get("text", "") or ""), snippet
        for key in ("list", "text_blocks", "blocks", "items"):
            for child in node.get(key, []) or []:
                yield from _iter_snippet_links(child)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_snippet_links(item)


def discover_handles(
    name: str, profession: str = "", suffix: Optional[str] = None
) -> Dict[str, List[dict]]:
    """
    Google-AI-Mode discovery for one talent. Extracts the profile links Google AI
    Mode cites (from the structured ``snippet_links``, then ``references``, with a
    regex backstop) and returns ``{platform: [{url, source:'serpapi', meta{...}}]}``.
    No LLM is involved — the caller tags these Manual Review Needed.
    Returns ``{}`` on any failure so the row still completes.

    The query is ``"<name> <profession> <suffix>"``. ``suffix`` defaults to the
    configured ``_QUERY_SUFFIX``; pass a string (e.g. the operator's custom query
    prompt from the "Without Wikipedia" UI) to override it — an empty string is a
    valid override that drops the suffix entirely.
    """
    if not is_configured():
        print("  [SERPAPI] Skipped — SERPAPI_API_KEY not set.")
        return {}

    effective_suffix = _QUERY_SUFFIX if suffix is None else suffix.strip()
    query = " ".join(
        part for part in (name.strip(), (profession or "").strip(), effective_suffix) if part
    )
    try:
        data = _search(query)
    except Exception as exc:  # noqa: BLE001 — never abort the row on SerpApi failure
        print(f"  [SERPAPI] search failed for '{name}': {exc.__class__.__name__}: {exc}")
        return {}

    if isinstance(data, dict) and data.get("error"):
        print(f"  [SERPAPI] API error for '{name}': {data['error']}")
        return {}

    by_platform: Dict[str, List[dict]] = {}
    seen: Dict[str, set] = {}

    def _add(url: str, context: str = "") -> None:
        url = _clean_url(url)
        low = url.lower()
        if any(junk in low for junk in _JUNK_HOSTS):
            return
        platform = social_urls.platform_from_url(url)
        if not platform or not social_urls.is_valid_profile_url(url, platform):
            return
        norm = social_urls.normalize_profile_url(url, platform)
        bucket = seen.setdefault(platform, set())
        if norm in bucket:
            return
        bucket.add(norm)
        by_platform.setdefault(platform, []).append({
            "url": norm,
            "source": "serpapi",
            "meta": {"serper_snippet": context.strip() or "Listed by Google AI Mode."},
        })

    def _add_snippet_entry(link: str, text: str, context: str) -> None:
        """
        Add one ``snippet_links`` entry. Google AI Mode is inconsistent: sometimes
        ``link`` is a full profile URL (added directly), and sometimes it is only
        the bare platform homepage (e.g. ``https://www.tiktok.com``) with the real
        username sitting in ``text`` (e.g. ``@michelle.and.andy``). In that second
        case we rebuild ``platform.com/@handle`` from the handle, so the profile is
        not silently dropped just because the link lacked the username path.
        """
        _add(link, context)
        platform = social_urls.platform_from_url(link)
        # Only reconstruct when the link is a bare platform root (not already a
        # valid profile) and the text is a clean single-token handle.
        if platform and not social_urls.is_valid_profile_url(link, platform):
            handle = (text or "").strip().lstrip("@").strip("/")
            if re.fullmatch(r"[A-Za-z0-9._-]{1,50}", handle or ""):
                rebuilt = social_urls.profile_url_from_handle(handle, platform)
                if rebuilt:
                    _add(rebuilt, context)

    # 1) Structured snippet_links (the format Google AI Mode returns).
    for link, text, snippet in _iter_snippet_links(data.get("text_blocks", [])):
        _add_snippet_entry(link, text, snippet or text)
    # 2) Cited references.
    for ref in data.get("references", []) or []:
        if isinstance(ref, dict) and ref.get("link"):
            _add(ref["link"], str(ref.get("title", "") or ""))
    # 3) Regex backstop over the whole payload (catches links only in prose).
    for raw in _URL_RE.findall(json.dumps(data, ensure_ascii=False)):
        _add(raw)
    # 4) Prose backstop: handles named only in the AI answer text with NO link
    #    element (e.g. "TikTok: @handle"). Rebuild the profile URL per platform.
    #    Dedup in _add means anything already found above is not re-added.
    for platform, handle in _iter_prose_handles(data.get("reconstructed_markdown", "") or ""):
        rebuilt = social_urls.profile_url_from_handle(handle, platform)
        if rebuilt:
            _add(rebuilt, "Handle named in Google AI Mode answer text.")

    total = sum(len(v) for v in by_platform.values())
    print(f"  [SERPAPI] '{query}' -> {total} link(s) across {len(by_platform)} platform(s)")
    return by_platform
