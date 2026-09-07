"""
social_urls.py  —  Shared social-platform URL utilities
========================================================

Single source of truth for:
  • the target platforms and their domains
  • detecting which platform a URL belongs to
  • validating that a URL is a *profile/channel* URL (not a post/reel/video)
  • normalising a profile URL to a canonical form
  • extracting the handle/username from a profile URL

Used by apify_service, serper_service and verification_pipeline so the
platform rules live in exactly one place.

The profile-validation rules are ported from the previous pipeline
(``testing.is_valid_profile_url``) so behaviour stays consistent.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional
from urllib.parse import urlparse

# Target platforms (output schema) and the domains that identify each one.
# Order matches the export schema: Instagram, Facebook, YouTube, TikTok, X.
# (Apify covers the first four; X is discovered via Serper only.)
PLATFORMS: Dict[str, List[str]] = {
    "Instagram": ["instagram.com"],
    "Facebook":  ["facebook.com"],
    "YouTube":   ["youtube.com", "youtu.be"],
    "TikTok":    ["tiktok.com"],
    "X":         ["x.com", "twitter.com"],
}

# x.com / twitter.com path segments that are site features — NOT user handles.
_X_NON_PROFILE_HANDLES = frozenset({
    "home", "explore", "search", "notifications", "messages", "settings",
    "login", "logout", "signup", "signin", "sign_in", "register",
    "intent", "share", "compose", "post", "i", "pic", "photo", "photos",
    "hashtag", "lists", "topics", "who_to_follow", "account", "accounts",
    "analytics", "pixel", "ads", "business", "about",
    "privacy", "tos", "help", "support", "legal", "oauth", "deck",
    "premium", "verified", "moments", "grok", "communities", "bookmarks",
    "status", "followers", "following", "likes", "media", "widgets",
    "embed", "redirect", "sessions", "rules", "safety",
})


def _x_handle_from_url(link: str) -> str:
    """First path segment for x.com / twitter.com URLs (empty if not a lone handle)."""
    try:
        path = (urlparse(link).path or "").strip("/")
    except Exception:
        return ""
    segs = [s for s in path.split("/") if s]
    return segs[0].lstrip("@") if len(segs) == 1 else ""


def _is_valid_x_profile_handle(handle: str) -> bool:
    if not handle:
        return False
    h = handle.lower().lstrip("@")
    if h in _X_NON_PROFILE_HANDLES:
        return False
    if h.startswith("analytics_") or h.endswith("_pixel"):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_]{1,15}", h))


def platform_from_url(url: str) -> Optional[str]:
    """Return the target platform key a URL belongs to, or None."""
    if not url or not isinstance(url, str):
        return None
    host = (urlparse(url).netloc or "").lower()
    for platform, domains in PLATFORMS.items():
        if any(domain in host for domain in domains):
            return platform
    return None


def is_valid_profile_url(link: str, platform: str) -> bool:
    """True only for genuine profile/channel URLs on the given platform."""
    if not isinstance(link, str) or not link.strip():
        return False
    u = link.strip()
    try:
        parsed = urlparse(u)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    full = u.lower()

    if platform == "Facebook":
        if "facebook.com" not in host:
            return False
        if any(seg in full for seg in ("/posts/", "/photos/", "/videos/", "/watch/",
                                       "/reel", "/story.php", "/permalink/")):
            return False
        if "profile.php" in path or "/people/" in path or "/pages/" in path:
            return True
        segs = [s for s in path.strip("/").split("/") if s]
        if len(segs) == 1 and segs[0] not in (
            "share", "sharer", "groups", "events", "marketplace", "gaming", "watch"
        ):
            return True
        return False

    if platform == "Instagram":
        if "instagram.com" not in host:
            return False
        if any(x in full for x in ("/p/", "/reel", "/reels/", "/stories/",
                                   "/tv/", "/explore/", "/tags/", "/locations/")):
            return False
        segs = [s for s in path.strip("/").split("/") if s]
        return len(segs) == 1

    if platform == "YouTube":
        if "youtube.com" not in host and "youtu.be" not in host:
            return False
        if any(x in full for x in ("/watch", "/shorts", "/playlist", "/results",
                                   "/live/", "/feed/", "/attribution_link")):
            return False
        return "/@" in full or "/channel/" in full or "/c/" in full or "/user/" in full

    if platform == "X":
        if "x.com" not in host and "twitter.com" not in host:
            return False
        if "/status/" in full or "/i/" in full or "/intent/" in full or "/search" in full:
            return False
        segs = [s for s in path.strip("/").split("/") if s]
        if len(segs) != 1:
            return False
        return _is_valid_x_profile_handle(segs[0])

    if platform == "TikTok":
        if "tiktok.com" not in host:
            return False
        if any(x in full for x in ("/video/", "/tag/", "/music/", "/discover", "/foryou")):
            return False
        return bool(re.search(r"tiktok\.com/@[^/]+/?$", full))

    return False


def normalize_profile_url(url: str, platform: str) -> str:
    """Canonicalise a profile URL (strip trailing slash, normalise YT host)."""
    if not url or not isinstance(url, str):
        return ""
    u = url.strip()
    if platform == "X":
        # twitter.com and x.com are the same account. Without this the same
        # profile from two sources never dedupes, and a link that matches the
        # client's record reads as a mismatch.
        u = re.sub(r"^(https?://)(www\.|mobile\.)?twitter\.com", r"\1x.com", u, flags=re.I)
        u = re.sub(r"^(https?://)(www\.|mobile\.)?x\.com", r"\1x.com", u, flags=re.I)
    if platform == "YouTube":
        u = u.replace("://m.youtube.com", "://www.youtube.com")
        u = u.replace("://music.youtube.com", "://www.youtube.com")
        netloc = urlparse(u).netloc
        if "youtube.com" in u and "www." not in netloc and "m." not in netloc:
            u = u.replace("://youtube.com", "://www.youtube.com")
    return u.rstrip("/")


# Templates for rebuilding a canonical profile URL from a bare handle.
_HANDLE_URL_TEMPLATES: Dict[str, str] = {
    "Instagram": "https://www.instagram.com/{}",
    "Facebook":  "https://www.facebook.com/{}",
    "X":         "https://x.com/{}",
    "TikTok":    "https://www.tiktok.com/@{}",
    "YouTube":   "https://www.youtube.com/@{}",
}

# A raw YouTube channel id (UC + 22 chars) needs the /channel/ form, not /@.
_YT_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


def profile_url_from_handle(handle: str, platform: str) -> str:
    """Build a canonical profile URL from a bare handle ('trevorstj' -> full URL)."""
    h = (handle or "").strip().lstrip("@").strip("/")
    if not h or platform not in _HANDLE_URL_TEMPLATES:
        return ""
    if platform == "YouTube" and _YT_CHANNEL_ID_RE.match(h):
        return f"https://www.youtube.com/channel/{h}"
    return _HANDLE_URL_TEMPLATES[platform].format(h)


def coerce_profile_url(value: object, platform: str) -> str:
    """
    Turn a spreadsheet cell into a canonical profile URL for ``platform``.

    Input columns are inconsistent in the wild, so this accepts all the shapes
    seen in the brand-definition export:
      • a full URL           "http://twitter.com/trevorstjohn"
      • a bare handle        "trevorstj"
      • an @handle           "@scotteller21"
      • a flag/URL pair      "FALSE|http://www.facebook.com/actortrevorstjohn"

    Returns "" when the value can't be resolved to a VALID profile URL for that
    platform, so a junk cell can never enter the candidate set.
    """
    raw = str(value or "").strip()
    # Guard the null-ish spellings that reach here from spreadsheets: a pandas
    # NaN stringifies to "nan" and would otherwise become the handle "nan".
    if not raw or raw.lower() in ("nan", "none", "null", "n/a", "-", "#n/a"):
        return ""
    # "TRUE|http://twitter.com/ScotTeller21" -> prefer the URL half.
    if "|" in raw:
        parts = [p.strip() for p in raw.split("|") if p.strip()]
        if not parts:
            return ""
        raw = next((p for p in parts if p.lower().startswith("http")), parts[0])
    tokens = raw.split()
    if not tokens:
        return ""
    raw = tokens[0].strip()

    if raw.lower().startswith(("http://", "https://")):
        url = "https://" + raw.split("://", 1)[1]  # normalise the scheme
        if platform_from_url(url) != platform:
            return ""  # column says one platform, value points at another
    else:
        url = profile_url_from_handle(raw, platform)

    if not url or not is_valid_profile_url(url, platform):
        return ""
    return normalize_profile_url(url, platform)


def handle_from_url(url: str, platform: str) -> str:
    """Best-effort username/handle for a profile URL (used for LLM signals)."""
    if not url:
        return ""
    path = (urlparse(url).path or "").strip("/")
    if platform == "X":
        return _x_handle_from_url(url)
    if platform == "TikTok":
        m = re.search(r"@([^/]+)", path)
        return m.group(1) if m else ""
    if platform == "YouTube":
        m = re.search(r"(?:@|channel/|c/|user/)([^/]+)", path)
        return (m.group(1).lstrip("@") if m else "")
    segs = [s for s in path.split("/") if s]
    return segs[0].lstrip("@") if segs else ""
