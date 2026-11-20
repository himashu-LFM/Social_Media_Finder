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
from typing import Any, Dict, List, Optional

import requests

from app.common.retry import request_with_retry

from app.platforms import social_urls

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


# Status-code (404) existence platforms. Facebook/Instagram 200 for everything, so
# they get their own og:title checks in ``profile_is_missing``; TikTok has no signal.
_EXISTENCE_CHECK_PLATFORMS = frozenset({"YouTube", "X"})

_MISSING_CACHE: Dict[str, bool] = {}
# Per-run cache of the SMALL liveness facts of each page (``_load_page``), so the
# liveness probe and enrichment share one download without retaining the raw HTML
# (YT pages run ~1 MB — keeping every page for a 500-brand run would risk an OOM).
_PAGE_CACHE: Dict[str, Dict[str, Any]] = {}

# Bounded per-run raw-HTML cache. Restores fetch dedup for a URL read more than once
# in a run (the liveness probe + enrichment, and the bio_links / aggregators follow-up
# fetches that call ``_fetch_html`` directly) while keeping memory capped: it is
# FIFO-evicted at a fixed size, so it can't grow without bound across a large run the
# way retaining every page's HTML would. Only successful (og-bearing) HTML is stored,
# so a transient block is never frozen and can be retried.
_HTML_CACHE: Dict[str, str] = {}
_HTML_CACHE_MAXSIZE = 64


def clear_cache() -> None:
    _CACHE.clear()
    _MISSING_CACHE.clear()
    _PAGE_CACHE.clear()
    _HTML_CACHE.clear()


def _facebook_page_is_dead(url: str) -> bool:
    """Facebook 200s for everything, so a 404 probe is useless: a real page has an
    og:title, a dead vanity returns the generic shell. Blocked/empty fetch -> kept."""
    facts = _load_page(url)
    if facts is None:
        return False
    return (not facts["og_present"]) and facts["fb_shell"]


# IG's generic shell appears for BOTH a missing username and a blocked view of a REAL
# profile, so only an explicit not-found marker (not a missing og:title) proves absence.
_IG_NOT_FOUND_RE = re.compile(
    r"this page isn[’']?t available|the link you followed may be broken|"
    r"page not found",
    re.I,
)


def _instagram_page_is_dead(url: str) -> bool:
    """Dead only when the body carries an explicit not-found marker: an og:title means
    alive, and a bare shell/blocked fetch is kept (it's also what a real profile returns)."""
    facts = _load_page(url)
    if facts is None:
        return False
    return (not facts["og_present"]) and facts["ig_not_found"]


def profile_is_missing(url: str, platform: str) -> bool:
    """
    True ONLY when the platform definitively reports the profile does not exist
    (conservative: any inconclusive response -> False). YouTube/X use a 404 probe;
    Facebook/Instagram use an og:title liveness check (they 200 for everything).
    """
    if not url:
        return False
    if url in _MISSING_CACHE:
        return _MISSING_CACHE[url]
    missing = False
    try:
        if platform == "Facebook":
            missing = _facebook_page_is_dead(url)
        elif platform == "Instagram":
            missing = _instagram_page_is_dead(url)
        elif platform in _EXISTENCE_CHECK_PLATFORMS:
            resp = requests.get(url, headers={**_BASE_HEADERS, "User-Agent": _USER_AGENTS[0]},
                                timeout=_TIMEOUT, allow_redirects=True)
            missing = resp.status_code == 404
    except Exception as exc:  # noqa: BLE001 — never let a probe fail a candidate
        print(f"  [EXISTS] probe failed {url[:60]}… : {exc.__class__.__name__}")
        missing = False
    _MISSING_CACHE[url] = missing
    return missing


_PRIVATE_TRUE_RE = re.compile(r'"is_private"\s*:\s*true|"isPrivate"\s*:\s*true', re.I)
_PRIVATE_FALSE_RE = re.compile(r'"is_private"\s*:\s*false|"isPrivate"\s*:\s*false', re.I)

# A YouTube channel's stable id (UC + 22 chars) lives in the inline JSON
# ("channelId"/"externalId") and the /channel/UC... link; it is rename-proof, so it
# lets two URL forms of one channel be recognised as the same.
_YT_CHANNEL_ID_JSON_RE = re.compile(r'"(?:channelId|externalId)"\s*:\s*"(UC[\w-]{22})"')
_YT_CHANNEL_ID_PATH_RE = re.compile(r'/channel/(UC[\w-]{22})')
# The canonical <link> / og:url tag names the page's OWN channel — unlike the inline
# JSON, which also lists recommended channels (its first match is often not the owner).
_YT_OWN_ID_TAG_RE = re.compile(
    r'''<(?:link|meta)\b[^>]*\b(?:rel=["']canonical["']|property=["']og:url["'])[^>]*>''',
    re.I)


def _page_is_private(html: str) -> bool:
    """
    True only when the page unambiguously declares the account private: a private
    flag AND no public flag. IG can embed other accounts, so a page with both flags
    is ambiguous and deferred (Apify's per-account field still catches those).
    """
    if not html:
        return False
    return bool(_PRIVATE_TRUE_RE.search(html)) and not bool(_PRIVATE_FALSE_RE.search(html))


def _youtube_channel_id_from_html(html: str) -> str:
    """The page's OWN channel id (UC...), or '' if none found.

    Reads the canonical <link>/og:url first — those name the page's own channel —
    before the inline JSON, whose first match is often a RECOMMENDED channel, not the
    owner (that mis-pick was rewriting correct channels to wrong ones).
    """
    if not html:
        return ""
    for tag in _YT_OWN_ID_TAG_RE.findall(html):
        m = _YT_CHANNEL_ID_PATH_RE.search(tag)
        if m:
            return m.group(1)
    m = _YT_CHANNEL_ID_JSON_RE.search(html) or _YT_CHANNEL_ID_PATH_RE.search(html)
    return m.group(1) if m else ""


def _html_cache_put(url: str, html: str) -> None:
    """Store HTML in the bounded per-run cache, evicting the oldest entry when full."""
    if url in _HTML_CACHE:
        return
    if len(_HTML_CACHE) >= _HTML_CACHE_MAXSIZE:
        _HTML_CACHE.pop(next(iter(_HTML_CACHE)), None)
    _HTML_CACHE[url] = html


def _fetch_html(url: str) -> str:
    """Fetch page HTML, trying each UA until OpenGraph tags appear. Successful HTML is
    kept in a small FIFO-bounded per-run cache so a URL read more than once in a run
    (the liveness probe + enrichment, and bio_links / aggregators follow-ups) is
    downloaded once, while memory stays capped — retaining every page would risk an OOM
    on ~1 MB pages. A failed/blocked fetch is NOT cached, so a transient block can be
    retried on a later pass rather than frozen as empty for the rest of the run."""
    cached = _HTML_CACHE.get(url)
    if cached is not None:
        return cached
    last = ""
    for ua in _USER_AGENTS:
        try:
            # request_with_retry, not a bare get: TikTok in particular resets
            # the TLS connection on a first contact often enough that a single
            # attempt returns nothing, the candidate looks evidence-free, and a
            # correct profile gets escalated to Manual Review for no reason.
            resp = request_with_retry("GET", url,
                                      headers={**_BASE_HEADERS, "User-Agent": ua},
                                      timeout=_TIMEOUT, allow_redirects=True)
            if not resp.ok or len(resp.content) > _MAX_BYTES:
                continue
            html = resp.text or ""
            last = html
            lowered = html.lower()
            if "og:title" in lowered or "og:description" in lowered:
                _html_cache_put(url, html)
                return html
        except Exception as exc:  # noqa: BLE001 — enrichment must never raise
            print(f"  [PROFILE] fetch failed {url[:70]}… : {exc.__class__.__name__}")
    return last


def _load_page(url: str) -> Optional[Dict[str, Any]]:
    """Fetch a page once and return only its CHEAP liveness facts — never raw HTML.

    The expensive enrichment parse (meta tags, verified / private flags, the YouTube
    channel id, and the bio-link scan over the whole page) is NOT done here — it lives
    in ``fetch_profile_metadata``, so a page that is only liveness-probed and never
    enriched doesn't pay for it. Cached per run; the raw HTML download itself is shared
    with enrichment through ``_fetch_html``'s cache. A failed fetch returns ``None`` and
    is NOT cached, so a transient block/throttle can be retried on a later pass.
    """
    if url in _PAGE_CACHE:
        return _PAGE_CACHE[url]
    html = _fetch_html(url)
    if not html:
        return None
    low = html.lower()
    facts: Dict[str, Any] = {
        "og_present": 'property="og:title"' in low or 'property="og:description"' in low,
        "fb_shell": "<title>facebook</title>" in low,
        "ig_not_found": bool(_IG_NOT_FOUND_RE.search(html)),
    }
    _PAGE_CACHE[url] = facts
    return facts


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
    # Shares the one download with the liveness probe via _fetch_html's cache; the full
    # parse below (incl. the bio-link scan) runs only on this enrichment path, never on
    # a bare liveness probe.
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

    # Enrichment-only signals, parsed here rather than on the liveness path: the
    # verified badge, the IG private flag (explicit "is_private":true only), the
    # YouTube channel id, and every social profile the page links to (the back-link
    # signal the pipeline checks against the client's own handles).
    if re.search(r'"is_verified"\s*:\s*true|"isVerified"\s*:\s*true|"verified"\s*:\s*true',
                 html, re.I):
        meta["verified"] = True
    if _page_is_private(html):
        meta["private"] = True
    if platform == "YouTube":
        # The URL's own /channel/UC... id is authoritative; the page's own id is used
        # only for a /@handle or /user/ form that doesn't already carry the id.
        m = _YT_CHANNEL_ID_PATH_RE.search(url)
        cid = m.group(1) if m else _youtube_channel_id_from_html(html)
        if cid:
            meta["channel_id"] = cid
    try:
        # Imported lazily — bio_links imports this module, so a top-level import
        # would be circular.
        from app.discovery import bio_links as bio_link_service
        links = bio_link_service.links_by_platform(html, exclude_url=url)
        if links:
            meta["profile_links"] = links
    except Exception as exc:  # noqa: BLE001 — enrichment must never raise
        print(f"  [PROFILE] link scan failed {url[:60]}… : {exc.__class__.__name__}")

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
    # NOTE: this uses its own flag. It previously shared `_enriched` with the
    # pipeline's Serper-context pass, which sets that flag on every candidate
    # before calling this function — so every candidate was skipped and the OG
    # fetch never ran at all. Follower counts, bios and verified badges were
    # silently absent from every verification.
    for cand in candidates:
        if cand.get("_og_enriched"):
            continue
        url = cand.get("url", "")
        if not url:
            continue
        fetched = fetch_profile_metadata(url, platform)
        merged = dict(fetched)
        merged.update({k: v for k, v in (cand.get("meta") or {}).items() if v not in ("", None)})
        cand["meta"] = merged
        cand["_og_enriched"] = True
