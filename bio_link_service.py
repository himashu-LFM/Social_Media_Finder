"""
bio_link_service.py  —  first-party link harvesting from an anchor profile

The idea in one line: **a link published in someone's own bio was put there by
them**, so it is stronger evidence of ownership than any search result.

When a client file gives us an Instagram handle, that profile usually lists the
same person's YouTube, TikTok, X and Facebook. Reading them costs one HTTP fetch
and replaces up to four Serper searches and four LLM adjudications — cheaper AND
more reliable, because the search path can only ever infer what the bio states
outright.

Scope, deliberately narrow:

* This module **finds** links. It does not decide identity, and it does not
  score anything — that stays in ``verification_service``.
* It runs only in custom (non-Wikipedia) mode. The Wikipedia flow is measured
  and tuned, and nothing here touches it.
* It is best-effort. Instagram serves logged-out visitors a stripped page and
  sometimes an interstitial; a harvest that finds nothing returns ``{}`` and the
  normal discovery pipeline runs exactly as before. It never raises.

What it does NOT do: no login, no cookie replay, no anti-bot evasion. If the
platform does not show it to an anonymous visitor, we do not have it.
"""

from __future__ import annotations

import html as _html
import re
import threading
from typing import Dict, List, Tuple
from urllib.parse import parse_qs, unquote, urlparse

import profile_metadata
import social_urls

# Platforms whose profile pages are worth reading for outbound links, in the
# order we would rather anchor on. Instagram first because that is the column
# the client actually fills in; YouTube second because its About panel lists
# links explicitly and it blocks anonymous readers far less often.
ANCHOR_PRIORITY: Tuple[str, ...] = ("Instagram", "YouTube")

# Platforms wrap outbound links in a redirector. The real destination is in a
# query parameter, so an un-unwrapped link classifies as the wrong platform.
_REDIRECT_HOSTS: Dict[str, Tuple[str, ...]] = {
    "l.instagram.com": ("u",),
    "l.facebook.com": ("u",),
    "lm.facebook.com": ("u",),
    "www.youtube.com": ("q",),      # /redirect?q=
    "youtube.com": ("q",),
    "out.reddit.com": ("url",),
    "t.umblr.com": ("z",),
}

# Bare URLs written into bio text ("yt: youtube.com/@someone").
_BARE_URL_RE = re.compile(r"""(?:https?://|www\.)[^\s"'<>\\)\]]+""", re.I)
_HREF_RE = re.compile(r"""<a\b[^>]*?href\s*=\s*["']([^"']+)["']""", re.I)

# Schemeless platform mentions — "twitter.com/kako", "tiktok.com/@kako" — which
# is how a handle most often gets written into a bio.
_SCHEMELESS_RE = re.compile(
    r"""(?<![\w./@-])((?:www\.)?(?:instagram|facebook|youtube|tiktok|twitter|x)\.com/[^\s"'<>\\)\]]+)""",
    re.I,
)

# JSON blobs carry the same links backslash-escaped (``https:\/\/x.com\/kako``).
# Catching them is what makes this work on Instagram, whose visible HTML is
# nearly empty — the real payload is a script tag.
_JSON_URL_RE = re.compile(r'"(https?:\\?/\\?/[^"\s]{6,300}?)"')

# Handles that are platform plumbing, not people. Every one of these was
# observed being harvested from a real page: Instagram's logged-out profile
# markup, for example, contains ``facebook.com/ig_xsite_user_info``, which is
# structurally a valid Facebook profile URL and is nobody's account.
_CHROME_HANDLES = frozenset({
    "ig_xsite_user_info", "instagram", "facebook", "youtube", "tiktok", "twitter",
    "x", "meta", "google", "help", "support", "about", "privacy", "policies",
    "terms", "legal", "login", "signup", "explore", "developers", "business",
    "creators", "ads", "press", "jobs", "careers", "blog", "shop", "download",
})

_CACHE: Dict[str, Dict[str, str]] = {}
_LOCK = threading.Lock()


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def is_platform_chrome(url: str, platform: str) -> bool:
    """
    True for a structurally valid profile URL that is really site furniture.

    Shared with ``apify_service`` so both discovery paths reject the same junk —
    Apify scrapes the same pages and picks up the same ``ig_xsite_user_info``.
    """
    handle = social_urls.handle_from_url(url, platform).lstrip("@").lower()
    return handle in _CHROME_HANDLES


# ────────────────────────────────────────────────────────────────────────────
#  URL extraction
# ────────────────────────────────────────────────────────────────────────────

def unwrap_redirect(url: str) -> str:
    """``l.instagram.com/?u=https%3A//youtube.com/@x`` -> the YouTube URL."""
    current = url
    for _ in range(3):  # bounded: redirectors occasionally nest
        try:
            parsed = urlparse(current)
        except ValueError:
            return current
        params = _REDIRECT_HOSTS.get((parsed.netloc or "").lower())
        if not params:
            return current
        query = parse_qs(parsed.query)
        target = next((query[p][0] for p in params if query.get(p)), "")
        if not target:
            return current
        current = unquote(target)
    return current


def _candidate_urls(html_text: str) -> List[str]:
    """Every URL-shaped string on the page, from all three encodings."""
    found: List[str] = []
    found.extend(_HREF_RE.findall(html_text))
    found.extend(m.group(0) for m in _BARE_URL_RE.finditer(html_text))
    found.extend(m.group(1).replace("\\/", "/") for m in _JSON_URL_RE.finditer(html_text))
    found.extend(m.group(1) for m in _SCHEMELESS_RE.finditer(html_text))

    out: List[str] = []
    seen: set = set()
    for raw in found:
        url = _html.unescape(raw).strip().rstrip(".,);'\"")
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url.lstrip("/")
        url = unwrap_redirect(url)
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def links_by_platform(html_text: str, exclude_platform: str = "",
                      exclude_url: str = "") -> Dict[str, str]:
    """
    Classify a page's outbound URLs into one profile URL per platform.

    First occurrence wins. Pages list the profile's own links near the top and
    platform chrome ("Follow us on Instagram") in the footer, so document order
    is a genuinely good tiebreak — but see the caveat in ``harvest``.
    """
    picked: Dict[str, str] = {}
    exclude_norm = ""
    if exclude_url and exclude_platform:
        exclude_norm = social_urls.normalize_profile_url(exclude_url, exclude_platform)

    for url in _candidate_urls(html_text):
        platform = social_urls.platform_from_url(url)
        if not platform or platform in picked:
            continue
        # The anchor's own platform is skipped: we already have that profile,
        # and its page is full of internal links that would match it.
        if platform == exclude_platform:
            continue
        if not social_urls.is_valid_profile_url(url, platform):
            continue
        if is_platform_chrome(url, platform):
            continue
        normalized = social_urls.normalize_profile_url(url, platform)
        if normalized and normalized != exclude_norm:
            picked[platform] = normalized
    return picked


# ────────────────────────────────────────────────────────────────────────────
#  Harvest
# ────────────────────────────────────────────────────────────────────────────

def _pages_for(anchor_url: str, platform: str) -> List[str]:
    """Which page(s) to read for this anchor."""
    if platform == "YouTube":
        # The About tab is where a channel's links actually live; the channel
        # home page frequently does not render them for an anonymous visitor.
        return [anchor_url.rstrip("/") + "/about", anchor_url]
    return [anchor_url]


def harvest(anchor_url: str, anchor_platform: str) -> Dict[str, str]:
    """
    Read ``anchor_url`` and return ``{platform: profile_url}`` for the links it
    publishes. Returns ``{}`` on a blocked page, an empty bio, or any error —
    an empty harvest is a normal outcome, not a failure, and the caller simply
    falls through to ordinary discovery.

    Note on trust: this establishes that the anchor profile *links to* these
    URLs. It does not establish that the anchor profile is who the file says it
    is. That assumption comes from the client's spreadsheet, and the caller
    records it in the reason text so it stays visible.
    """
    if not anchor_url or anchor_platform not in social_urls.PLATFORMS:
        return {}

    key = f"{anchor_platform}|{anchor_url}"
    with _LOCK:
        if key in _CACHE:
            return dict(_CACHE[key])

    found: Dict[str, str] = {}
    for page in _pages_for(anchor_url, anchor_platform):
        try:
            html_text = profile_metadata._fetch_html(page)
        except Exception as exc:  # noqa: BLE001 — harvesting must never raise
            print(f"  [BIO-LINKS] fetch failed {page[:70]}… : {exc.__class__.__name__}")
            continue
        if not html_text:
            continue
        for platform, url in links_by_platform(
            html_text, exclude_platform=anchor_platform, exclude_url=anchor_url
        ).items():
            found.setdefault(platform, url)
        if found:
            break  # the first page that yields anything is the profile's own

    if found:
        print(f"  [BIO-LINKS] {anchor_platform} {anchor_url[:56]}… -> "
              f"{', '.join(sorted(found))}")
    else:
        print(f"  [BIO-LINKS] {anchor_platform} {anchor_url[:56]}… -> none "
              f"(blocked or no links published)")

    with _LOCK:
        _CACHE[key] = dict(found)
    return found


def links_from_url(profile_url: str, platform: str) -> Dict[str, str]:
    """
    Every platform link a single page publishes, INCLUDING its own platform.

    ``harvest`` deliberately excludes the anchor's own platform. This does not,
    because the back-link check needs exactly that: a YouTube channel that lists
    ``instagram.com/mettya_bizin`` is the case we care about.
    """
    try:
        html_text = profile_metadata._fetch_html(profile_url)
    except Exception as exc:  # noqa: BLE001
        print(f"  [BACKLINK] fetch failed {profile_url[:60]}… : {exc.__class__.__name__}")
        return {}
    if not html_text:
        return {}
    return links_by_platform(html_text, exclude_url=profile_url)


def backlink_check(candidate_url: str, candidate_platform: str,
                   client_handles: Dict[str, str]) -> Tuple[str, Dict[str, str]]:
    """
    Does this candidate link back to a profile the client already gave us?

    Returns ``(matched_client_url, other_links_on_the_page)``. The first is the
    client profile the candidate points at (``""`` if none); the second is every
    OTHER platform the same page lists, which — if the candidate turns out to be
    genuine — are that same subject's own links.

    Why this is worth a fetch: the client's Instagram handle is the one fact we
    have that a search cannot guess. A YouTube channel that independently names
    it is corroborating itself against our ground truth rather than against its
    own claims.
    """
    if not candidate_url or not client_handles:
        return "", {}

    published = links_from_url(candidate_url, candidate_platform)
    if not published:
        return "", {}

    matched = ""
    for platform, client_url in client_handles.items():
        listed = published.get(platform)
        if not listed or not client_url:
            continue
        # Handles, not URL strings — see _same_profile in verification_pipeline
        # for why comparing the normalised URLs is not reliable across hosts.
        listed_handle = social_urls.handle_from_url(listed, platform).lstrip("@").lower()
        client_handle = social_urls.handle_from_url(client_url, platform).lstrip("@").lower()
        if listed_handle and listed_handle == client_handle:
            matched = client_url
            break

    if not matched:
        return "", {}

    others = {
        platform: url for platform, url in published.items()
        if platform != candidate_platform and platform not in client_handles
    }
    print(f"  [BACKLINK] {candidate_platform} {candidate_url[:52]}… links back to "
          f"{matched} | also publishes: {', '.join(sorted(others)) or 'nothing else'}")
    return matched, others


def anchors(input_handles: Dict[str, str]) -> List[Tuple[str, str]]:
    """
    Client-provided handles worth reading, best first, as ``(platform, url)``.

    Only handles that came from the input file are eligible. A profile the
    pipeline discovered itself is not a trustworthy anchor — adopting links from
    an unverified discovery would let one wrong guess propagate to four cells.

    Instagram leads because that is the column clients actually fill in, but it
    is not the strongest reader: logged out, Instagram serves a stripped page
    and its bio links usually are not in it. YouTube's About panel almost always
    is. So we try Instagram, then fall through — measured on live profiles,
    YouTube returned the full set where Instagram returned nothing.
    """
    out: List[Tuple[str, str]] = []
    for platform in ANCHOR_PRIORITY:
        url = (input_handles or {}).get(platform, "")
        if url and social_urls.is_valid_profile_url(url, platform):
            out.append((platform, url))
    return out


def pick_anchor(input_handles: Dict[str, str]) -> Tuple[str, str]:
    """The single best anchor, or ``("", "")``. Kept for callers wanting one."""
    found = anchors(input_handles)
    return found[0] if found else ("", "")
