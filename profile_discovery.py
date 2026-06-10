"""
profile_discovery.py — Wikipedia + Serper + LLM profile discovery
=================================================================

Implements the talent social-profile discovery workflow:

  Step 1  Wikipedia/Wikidata metadata extraction          -> extract_wikipedia_metadata()
  Step 2  Existing Wikipedia/Wikidata social links        -> get_wikipedia_socials()
  Step 3  Serper search (per missing platform, w/ retry)  -> serper_search_with_retry()
  Step 4  Candidate URL selection (top-N as returned)     -> select_candidates()
  Step 5  LLM verification (choose from candidates only)  -> llm_verify()
  Step 6  Confidence threshold gate                       -> PROFILE_MATCH_THRESHOLD
  Step 7  Final per-platform result object + debug log    -> discover_platform()/discover_talent()

Step 4 — simplified candidate selection (2024-06 change)
--------------------------------------------------------
To maximise profile discovery we DO NOT rank, score, weight, or apply
profile-shape filtering to Serper results. Google/Serper ordering is trusted:
the correct profile almost always appears in the first few results, and the LLM
(Step 5) makes the final call. `select_candidates` therefore just takes the
top-N links exactly as returned, dropping only empty/null + duplicate links.
The single remaining guard is an OPTIONAL host check (on by default) so the
Instagram column never receives a Facebook/news URL — disable it with
RESTRICT_CANDIDATE_HOSTS=0 for a truly raw top-N.

Design notes
------------
* Self-contained: this module only depends on `requests` and `wikidata_lookup`.
  It deliberately does NOT import `testing` so `testing` can import it without
  a circular dependency.
* Platform filters are configurable via the PLATFORM_CONFIG dict below and can
  be overridden at runtime with the PLATFORM_FILTERS_JSON env var.
* The per-platform return shape is exactly:
      {talent_name, platform, profile_url, source, confidence, reasoning}
  with source ∈ {"WIKIPEDIA", "LLM_VERIFIED", "NOT_FOUND"}.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests

import wikidata_lookup

# The pipeline (and wikidata_lookup) print Unicode status glyphs (✓, ═, …).
# On Windows the default console codec is cp1252, which raises
# UnicodeEncodeError and can abort a lookup mid-flight. Force UTF-8 so those
# prints never crash the job. Best-effort; safe to skip if unsupported.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass

# ─────────────────────────────────────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────────────────────────────────────
logger = logging.getLogger("profile_discovery")
if not logging.getLogger().handlers and not logger.handlers:
    # Make logs visible even when run from the threaded API job.
    logging.basicConfig(
        level=os.environ.get("PROFILE_DISCOVERY_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIG (all overridable via environment)
# ─────────────────────────────────────────────────────────────────────────────
SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_CHAT_MODEL = os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")

# Step 6 — configurable confidence threshold.
# Lowered from 0.75 -> 0.50 to prefer likely matches over excessive NOT_FOUND.
# Tune per environment via the PROFILE_MATCH_THRESHOLD env var.
PROFILE_MATCH_THRESHOLD = float(os.environ.get("PROFILE_MATCH_THRESHOLD", "0.50"))

# Step 4 — how many candidate URLs to hand to the LLM (top-N as Serper returned).
MAX_CANDIDATES_FOR_LLM = int(os.environ.get("MAX_CANDIDATES_FOR_LLM", "4"))
SERPER_RESULTS_PER_QUERY = int(os.environ.get("SERPER_RESULTS_PER_QUERY", "10"))

# Step 4 — keep only links on the platform's own domain (e.g. instagram.com for
# the Instagram query). This is platform-correctness, NOT ranking/scoring.
# Set RESTRICT_CANDIDATE_HOSTS=0 to send a truly raw top-N (any host) to the LLM.
RESTRICT_CANDIDATE_HOSTS = os.environ.get("RESTRICT_CANDIDATE_HOSTS", "1").strip().lower() not in (
    "0", "false", "no", "off", ""
)

# Retry knobs.
SERPER_MAX_RETRIES = int(os.environ.get("SERPER_MAX_RETRIES", "3"))
LLM_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "3"))
RETRY_BACKOFF_SECONDS = float(os.environ.get("RETRY_BACKOFF_SECONDS", "1.0"))

# Wikipedia social links carry this confidence (most reliable source).
WIKIPEDIA_CONFIDENCE = float(os.environ.get("WIKIPEDIA_CONFIDENCE", "0.97"))

SOURCE_WIKIPEDIA = "WIKIPEDIA"
SOURCE_LLM = "LLM_VERIFIED"
SOURCE_NOT_FOUND = "NOT_FOUND"

# Target platforms (keys/order intentionally match testing.PLATFORMS so the
# existing wide-Excel schema and the frontend stay unchanged).
TARGET_PLATFORMS = ["Facebook", "Instagram", "X", "TikTok", "YouTube"]

_HTTP_HEADERS = {
    "User-Agent": (
        "SocialMediaFinder/1.0 (talent research tool) python-requests"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# Markers in a Serper/LLM error that mean "do not retry — stop now".
_FATAL_API_MARKERS = (
    "not enough credits", "unauthorized", "invalid api key",
    "forbidden", "quota", "billing", "exceeded your current quota",
)


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 4 — PLATFORM FILTER CONFIG (configurable)
# ─────────────────────────────────────────────────────────────────────────────
# Each platform defines:
#   query_term       -> appended to the talent name to build the Serper query
#   allowed_hosts    -> hostnames a candidate URL must belong to
#   profile_regex    -> a candidate must look like a *profile* URL
#   reject_substrings-> drop news/aggregator/post/reel/login/etc. URLs
PLATFORM_CONFIG: Dict[str, dict] = {
    "Instagram": {
        "query_term": "Instagram",
        "allowed_hosts": ["instagram.com"],
        "profile_regex": re.compile(r"^https?://(?:www\.)?instagram\.com/([^/?#]+)/?$", re.I),
        "reject_substrings": [
            "/p/", "/reel/", "/reels/", "/stories/", "/explore/", "/tags/",
            "/accounts/", "/about/", "/directory/", "/locations/", "?",
        ],
    },
    "Facebook": {
        "query_term": "Facebook",
        "allowed_hosts": ["facebook.com", "fb.com"],
        "profile_regex": re.compile(
            r"^https?://(?:www\.|m\.)?(?:facebook|fb)\.com/(?:pages/)?([^/?#]+)/?$", re.I
        ),
        "reject_substrings": [
            "/sharer", "/share", "/login", "/events", "/groups", "/marketplace",
            "/watch", "/gaming", "/hashtag", "/public", "/help", "/photo",
            "/photos", "/posts", "/story", "/permalink", "/notes",
        ],
    },
    "X": {
        "query_term": "X",
        "allowed_hosts": ["x.com", "twitter.com"],
        "profile_regex": re.compile(r"^https?://(?:www\.)?(?:x|twitter)\.com/([^/?#]+)/?$", re.I),
        "reject_substrings": [
            "/status/", "/hashtag/", "/search", "/intent", "/i/", "/home",
            "/explore", "/login", "/compose", "/share", "/notifications",
        ],
    },
    "TikTok": {
        "query_term": "TikTok",
        "allowed_hosts": ["tiktok.com"],
        "profile_regex": re.compile(r"^https?://(?:www\.)?tiktok\.com/@([^/?#]+)/?$", re.I),
        "reject_substrings": [
            "/video/", "/tag/", "/music/", "/discover", "/foryou", "/explore",
            "/upload", "/login", "/search",
        ],
    },
    "YouTube": {
        "query_term": "YouTube",
        "allowed_hosts": ["youtube.com"],
        "profile_regex": re.compile(
            r"^https?://(?:www\.)?youtube\.com/(?:@([^/?#]+)|channel/([^/?#]+)|c/([^/?#]+)|user/([^/?#]+))/?$",
            re.I,
        ),
        "reject_substrings": [
            "/watch", "/results", "/shorts/", "/playlist", "/feed", "/hashtag/",
            "/embed/", "/login",
        ],
    },
}

# X path segments that are site features, not handles.
_X_RESERVED = frozenset({
    "home", "explore", "search", "notifications", "messages", "settings",
    "login", "signup", "intent", "share", "compose", "i", "hashtag",
    "about", "tos", "privacy", "help", "premium", "verified",
})
# Generic reserved handles that are never a person/brand profile.
_RESERVED_HANDLES = frozenset({
    "share", "sharer", "login", "signup", "home", "explore", "about",
    "help", "privacy", "terms", "ads", "business", "directory", "watch",
})


def _apply_filter_overrides() -> None:
    """Merge PLATFORM_FILTERS_JSON env override into PLATFORM_CONFIG.

    Example:
        PLATFORM_FILTERS_JSON='{"Facebook": {"allowed_hosts": ["facebook.com"]}}'
    Only `allowed_hosts`, `reject_substrings` and `query_term` are mergeable.
    """
    raw = os.environ.get("PLATFORM_FILTERS_JSON", "").strip()
    if not raw:
        return
    try:
        override = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Ignoring invalid PLATFORM_FILTERS_JSON: %s", exc)
        return
    for platform, cfg in (override or {}).items():
        if platform not in PLATFORM_CONFIG or not isinstance(cfg, dict):
            continue
        for key in ("allowed_hosts", "reject_substrings", "query_term"):
            if key in cfg:
                PLATFORM_CONFIG[platform][key] = cfg[key]
                logger.info("Override %s.%s from PLATFORM_FILTERS_JSON", platform, key)


_apply_filter_overrides()


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 1 — WIKIPEDIA / WIKIDATA METADATA EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

# Wikidata properties we surface as structured metadata.
#   prop -> (output_key, value_kind)  where value_kind ∈ {"entity", "time", "string"}
_METADATA_PROPS = {
    "P106": ("occupation", "entity"),
    "P27": ("nationality", "entity"),
    "P569": ("birth_date", "time"),
    "P54": ("sports_team", "entity"),
    "P102": ("political_party", "entity"),
    "P108": ("organization", "entity"),
    "P641": ("sport", "entity"),
    "P452": ("industry", "entity"),
    "P856": ("website", "string"),
}


def _wikidata_claim_raw_values(entity: dict, prop: str) -> List[dict]:
    out = []
    for claim in entity.get("claims", {}).get(prop, []) or []:
        snak = claim.get("mainsnak", {})
        if snak.get("snaktype") == "value":
            out.append(snak.get("datavalue", {}).get("value"))
    return [v for v in out if v is not None]


def _resolve_entity_labels(qids: List[str]) -> Dict[str, str]:
    """Batch-resolve Wikidata entity QIDs to English labels."""
    qids = [q for q in qids if isinstance(q, str) and re.fullmatch(r"Q\d+", q)]
    if not qids:
        return {}
    labels: Dict[str, str] = {}
    # wbgetentities accepts up to 50 ids per call.
    for i in range(0, len(qids), 50):
        batch = qids[i:i + 50]
        try:
            resp = requests.get(
                "https://www.wikidata.org/w/api.php",
                params={
                    "action": "wbgetentities",
                    "ids": "|".join(batch),
                    "props": "labels",
                    "languages": "en",
                    "format": "json",
                },
                headers=_HTTP_HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            ents = resp.json().get("entities", {})
            for qid, ent in ents.items():
                label = ent.get("labels", {}).get("en", {}).get("value")
                if label:
                    labels[qid] = label
        except Exception as exc:  # noqa: BLE001 — best-effort enrichment
            logger.warning("Label resolution failed for %s: %s", batch, exc)
    return labels


def _wikipedia_rest_summary(wikipedia_url: str) -> Dict[str, str]:
    """Fetch {title, description, summary} from the Wikipedia REST summary API."""
    try:
        parsed = urlparse(wikipedia_url)
        if "/wiki/" not in (parsed.path or ""):
            return {}
        page_title = parsed.path.split("/wiki/", 1)[1].strip("/")
        url = f"{parsed.scheme}://{parsed.netloc}/api/rest_v1/page/summary/{page_title}"
        resp = requests.get(url, headers=_HTTP_HEADERS, timeout=12)
        if not resp.ok:
            return {}
        data = resp.json()
        return {
            "title": str(data.get("title") or "").strip(),
            "description": str(data.get("description") or "").strip(),
            "summary": str(data.get("extract") or "").strip(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Wikipedia REST summary failed: %s", exc)
        return {}


def extract_wikipedia_metadata(wikipedia_url: str, talent: str = "") -> dict:
    """Step 1 — build a structured metadata object for the talent.

    Prefers Wikidata, then the Wikipedia REST summary. Always returns a dict
    (possibly sparse); never raises.
    """
    metadata: dict = {"name": talent or "", "wikidata_qid": None}

    wikipedia_url = (wikipedia_url or "").strip()
    qid = None
    try:
        if wikipedia_url:
            qid = wikidata_lookup._wikipedia_url_to_qid(wikipedia_url)
        if not qid and talent:
            qid = wikidata_lookup._name_to_qid(talent)
    except Exception as exc:  # noqa: BLE001
        logger.warning("QID lookup failed for %s: %s", talent, exc)

    # ── Wikidata structured claims ──
    if qid:
        metadata["wikidata_qid"] = qid
        entity = None
        try:
            entity = wikidata_lookup._fetch_wikidata_entity(qid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Wikidata entity fetch failed for %s: %s", qid, exc)

        if entity:
            # English label as the canonical name, if present.
            label = entity.get("labels", {}).get("en", {}).get("value")
            if label:
                metadata["name"] = label

            pending_entity_qids: List[str] = []
            entity_fields: Dict[str, List[str]] = {}

            for prop, (key, kind) in _METADATA_PROPS.items():
                values = _wikidata_claim_raw_values(entity, prop)
                if not values:
                    continue
                if kind == "entity":
                    qids = [v.get("id") for v in values if isinstance(v, dict)]
                    entity_fields[key] = qids
                    pending_entity_qids.extend([q for q in qids if q])
                elif kind == "time":
                    raw = values[0]
                    t = raw.get("time") if isinstance(raw, dict) else None
                    if t:  # "+1980-01-15T00:00:00Z" -> "1980-01-15"
                        m = re.match(r"[+-](\d{4}-\d{2}-\d{2})", t)
                        metadata[key] = m.group(1) if m else t.lstrip("+")
                elif kind == "string":
                    metadata[key] = str(values[0]).strip()

            labels = _resolve_entity_labels(pending_entity_qids)
            for key, qids in entity_fields.items():
                resolved = [labels[q] for q in qids if q in labels]
                if resolved:
                    # occupation/profession often the most useful single string.
                    metadata[key] = resolved if len(resolved) > 1 else resolved[0]

    # "profession" is a friendly alias of occupation for the prompt/spec.
    if "occupation" in metadata and "profession" not in metadata:
        occ = metadata["occupation"]
        metadata["profession"] = occ[0] if isinstance(occ, list) else occ

    # ── Wikipedia REST summary (description + known_for + summary) ──
    if wikipedia_url:
        rest = _wikipedia_rest_summary(wikipedia_url)
        if rest.get("title") and not metadata.get("name"):
            metadata["name"] = rest["title"]
        if rest.get("description"):
            metadata["known_for"] = rest["description"]
        if rest.get("summary"):
            metadata["summary"] = rest["summary"]

    # Drop empty values for a tidy object.
    return {k: v for k, v in metadata.items() if v not in (None, "", [])}


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 2 — EXISTING WIKIPEDIA / WIKIDATA SOCIAL LINKS
# ─────────────────────────────────────────────────────────────────────────────

def get_wikipedia_socials(
    talent: str,
    wikipedia_url: str = "",
    title_category: str = "",
    title_sub_category: str = "",
    target_platforms: Optional[List[str]] = None,
) -> Dict[str, str]:
    """Step 2 — return {platform: profile_url} already present in Wikipedia/Wikidata.

    Reuses the existing wikidata_lookup preflight (Wikidata properties, official
    website crawl, Wikipedia article scan). Platforms returned here are treated
    as VERIFIED_FROM_WIKIPEDIA and skip the Serper/LLM steps.
    """
    target_platforms = target_platforms or TARGET_PLATFORMS
    try:
        preflight = wikidata_lookup.run_wiki_preflight(
            talent,
            title_category=title_category,
            title_sub_category=title_sub_category,
            wikipedia_url=wikipedia_url,
            target_platforms=target_platforms,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Wikipedia preflight failed for %s: %s", talent, exc)
        return {}

    socials: Dict[str, str] = {}
    for platform, value in (preflight or {}).items():
        if platform.startswith("_") or platform not in target_platforms:
            continue
        url = value[0] if isinstance(value, (tuple, list)) else value
        if url:
            socials[platform] = url
    if socials:
        logger.info("[%s] Wikipedia socials: %s", talent, list(socials.keys()))
    return socials


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 3 — SERPER SEARCH (with retry)
# ─────────────────────────────────────────────────────────────────────────────

def build_query(talent: str, platform: str) -> str:
    """Step 3 — platform-specific Serper query, e.g. 'Ari Melber Instagram'."""
    term = PLATFORM_CONFIG.get(platform, {}).get("query_term", platform)
    return f"{talent} {term}".strip()


def _is_fatal_api_error(message: str) -> bool:
    msg = (message or "").lower()
    return any(marker in msg for marker in _FATAL_API_MARKERS)


def serper_search_with_retry(
    query: str,
    num_results: int = SERPER_RESULTS_PER_QUERY,
    max_retries: int = SERPER_MAX_RETRIES,
) -> List[dict]:
    """Step 3 — call the Serper API with retry/backoff on transient failures.

    Returns a list of {title, snippet, link}. Raises RuntimeError on a fatal
    error (bad key / no credits) so the caller can stop the whole job.
    """
    if not SERPER_API_KEY:
        raise RuntimeError("SERPER_API_KEY is not set.")

    url = "https://google.serper.dev/search"
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload = {"q": query, "num": max(1, min(num_results, 10))}

    last_err = ""
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            if resp.status_code >= 400:
                try:
                    detail = resp.json().get("message") or resp.text
                except ValueError:
                    detail = resp.text
                detail = f"Serper {resp.status_code}: {detail}"
                if _is_fatal_api_error(detail) or resp.status_code in (401, 402, 403):
                    raise RuntimeError(detail)
                raise requests.HTTPError(detail)  # transient -> retry

            data = resp.json()
            return [
                {
                    "title": item.get("title", "") or "",
                    "snippet": item.get("snippet", "") or "",
                    "link": item.get("link", "") or "",
                }
                for item in data.get("organic", [])
            ]
        except RuntimeError:
            raise  # fatal — do not retry
        except Exception as exc:  # noqa: BLE001 — transient (network/5xx/timeout)
            last_err = str(exc)
            logger.warning("Serper attempt %d/%d failed for '%s': %s",
                           attempt, max_retries, query, last_err)
            if attempt < max_retries:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    logger.error("Serper exhausted retries for '%s': %s", query, last_err)
    return []


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 4 — CANDIDATE URL SELECTION
# ─────────────────────────────────────────────────────────────────────────────

def _host_matches(url: str, allowed_hosts: List[str]) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    host = host[4:] if host.startswith("www.") else host
    return any(host == h or host.endswith("." + h) for h in allowed_hosts)


# YouTube path segments that are tabs/features, not channel identifiers.
_YOUTUBE_RESERVED = frozenset({
    "watch", "results", "playlist", "feed", "embed", "hashtag", "login",
    "shorts", "browse", "about", "premium", "upload", "account", "redirect",
    "oauth", "signin", "logout", "creators", "ads", "kids", "gaming", "music",
    "videos", "featured", "streams", "playlists", "community", "channels",
    "howyoutubeworks", "new", "trending",
})


def _normalize_youtube(url: str) -> Optional[str]:
    """YouTube needs special handling: tab suffixes (/videos, /about) and legacy
    vanity URLs (youtube.com/<name>) are valid profiles; videos/playlists are not."""
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    host = (parsed.hostname or "").lower()
    if host not in ("youtube.com", "www.youtube.com"):
        return None  # reject tv./music./studio. subdomains
    segs = [s for s in (parsed.path or "").split("/") if s]
    if not segs:
        return None
    first = segs[0]
    if first.startswith("@"):
        handle = first.lstrip("@")
        return f"https://www.youtube.com/@{handle}" if handle else None
    if first in ("channel", "c", "user") and len(segs) >= 2:
        return f"https://www.youtube.com/{first}/{segs[1]}"
    # Legacy vanity URL: youtube.com/<name>
    if len(segs) == 1 and first.lower() not in _YOUTUBE_RESERVED:
        return f"https://www.youtube.com/{first}"
    return None


def _normalize_candidate(url: str, platform: str) -> Optional[str]:
    """Return a clean profile URL for `platform`, or None if not a profile URL."""
    cfg = PLATFORM_CONFIG.get(platform)
    if not cfg or not url:
        return None
    url = url.strip().split("#", 1)[0]
    if not _host_matches(url, cfg["allowed_hosts"]):
        return None
    low = url.lower()
    if any(bad in low for bad in cfg["reject_substrings"]):
        return None
    if platform == "YouTube":
        return _normalize_youtube(url)
    m = cfg["profile_regex"].match(url)
    if not m:
        return None
    # First non-empty capture group is the handle / channel id.
    captured = next((g for g in m.groups() if g), "")
    handle = captured.strip("/").lstrip("@").lower()
    if not handle or handle in _RESERVED_HANDLES:
        return None
    if platform == "X" and handle in _X_RESERVED:
        return None
    return url.rstrip("/")


def select_candidates(
    results: List[dict],
    platform: str,
    top_n: int = MAX_CANDIDATES_FOR_LLM,
) -> List[str]:
    """Step 4 — take the top-N links exactly as Serper returned them.

    Deliberately performs NO ranking, scoring, weighting, or profile-shape
    filtering (no profile regex, no reject-substrings, no reserved-handle
    pruning). Serper/Google ordering is trusted and the LLM (Step 5) decides.
    The only pruning is:
      * drop empty/null links,
      * drop fragment-only duplicates of an already-kept link,
      * (optional, on by default) keep only links on the platform's own
        domain so the Instagram column never receives a Facebook/news URL.
        Disable with RESTRICT_CANDIDATE_HOSTS=0 for a truly raw top-N.

    `_normalize_candidate`/`_normalize_youtube` are retained for backward
    compatibility / reuse but are intentionally NOT applied here.
    """
    allowed_hosts = PLATFORM_CONFIG.get(platform, {}).get("allowed_hosts", [])
    selected: List[str] = []
    seen = set()
    for item in results:
        link = (item.get("link") or "").strip()
        if not link:
            continue
        # Only obvious cleanup: drop the #fragment and any trailing slash so
        # equivalent links dedupe. Query strings are preserved on purpose.
        link = link.split("#", 1)[0].rstrip("/")
        if not link:
            continue
        if RESTRICT_CANDIDATE_HOSTS and allowed_hosts and not _host_matches(link, allowed_hosts):
            continue
        key = link.lower()
        if key in seen:
            continue
        seen.add(key)
        selected.append(link)
        if len(selected) >= top_n:
            break
    return selected


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 5 — LLM VERIFICATION (choose only from provided candidates)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_json_obj(text: str) -> dict:
    if not text:
        raise ValueError("Empty LLM response.")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object in LLM response.")
    return json.loads(text[start:end + 1])


def _call_openai(messages: List[dict], max_retries: int = LLM_MAX_RETRIES) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set.")
    body = {"model": OPENAI_CHAT_MODEL, "temperature": 0, "messages": messages}
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}

    last_err = ""
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers, json=body, timeout=60,
            )
            if resp.status_code >= 400:
                try:
                    detail = resp.json().get("error", {}).get("message") or resp.text
                except ValueError:
                    detail = resp.text
                detail = f"OpenAI {resp.status_code}: {detail}"
                if _is_fatal_api_error(detail) or resp.status_code in (401, 403):
                    raise RuntimeError(detail)
                raise requests.HTTPError(detail)  # transient (incl. 429/5xx)
            return resp.json()["choices"][0]["message"]["content"]
        except RuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            logger.warning("OpenAI attempt %d/%d failed: %s", attempt, max_retries, last_err)
            if attempt < max_retries:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError(f"OpenAI exhausted retries: {last_err}")


def llm_verify(
    metadata: dict,
    platform: str,
    candidate_urls: List[str],
    max_retries: int = LLM_MAX_RETRIES,
) -> dict:
    """Step 5 — ask the LLM to pick the single correct profile from candidates.

    The LLM may ONLY return one of the provided candidate URLs (or NOT_FOUND).
    Returns {"selected_url": str, "confidence": float, "reasoning": str}.
    """
    if not candidate_urls:
        return {"selected_url": "", "confidence": 0.0, "reasoning": "No candidate URLs."}

    system_msg = (
        "You verify the official social media profile of a public figure. "
        "You must NEVER invent a URL. You must only select from the provided "
        "candidate URLs. A blank answer is better than a wrong one. "
        "Respond with strict JSON only."
    )
    user_msg = f"""You are verifying the official social media profile of a public figure.

Wikipedia Metadata:
{json.dumps(metadata, indent=2, ensure_ascii=True)}

Platform:
{platform}

Candidate URLs:
{json.dumps(candidate_urls, indent=2, ensure_ascii=True)}

Instructions:
1. Compare candidate URLs with the metadata.
2. Determine which URL most likely belongs to the same person.
3. Use occupation, profession, nationality, organization, team, and known-for information.
4. Return only one URL.
5. If confidence is low, return NOT_FOUND.
6. Never generate a URL.
7. Only select from the provided candidates.

Required JSON response (no markdown, no extra keys):
{{
  "selected_url": "",
  "confidence": 0.0,
  "reasoning": ""
}}"""

    try:
        content = _call_openai(
            [{"role": "system", "content": system_msg},
             {"role": "user", "content": user_msg}],
            max_retries=max_retries,
        )
        parsed = _extract_json_obj(content)
    except Exception as exc:  # noqa: BLE001
        logger.error("LLM verification failed for %s: %s", platform, exc)
        return {"selected_url": "", "confidence": 0.0, "reasoning": f"LLM error: {exc}"}

    selected = str(parsed.get("selected_url", "") or "").strip()
    try:
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    reasoning = str(parsed.get("reasoning", "") or "").strip()

    # Guardrail: the LLM must not invent a URL outside the candidate set.
    if selected and selected.upper() != SOURCE_NOT_FOUND:
        allowed = {c.rstrip("/").lower() for c in candidate_urls}
        if selected.rstrip("/").lower() not in allowed:
            logger.warning("LLM returned non-candidate URL '%s' — discarding.", selected)
            return {
                "selected_url": "",
                "confidence": min(confidence, 0.3),
                "reasoning": f"LLM returned a URL not in candidate set (discarded). {reasoning}",
            }
    else:
        selected = ""

    return {"selected_url": selected, "confidence": confidence, "reasoning": reasoning}


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 7 — DEBUG LOGGING HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _metadata_summary(metadata: dict) -> str:
    """Compact one-line summary of the Wikipedia metadata for debug logs."""
    keys = ("name", "occupation", "profession", "nationality",
            "organization", "sports_team", "known_for")
    parts: List[str] = []
    for key in keys:
        val = metadata.get(key)
        if not val:
            continue
        if isinstance(val, list):
            val = ", ".join(str(v) for v in val)
        parts.append(f"{key}={val}")
    return " | ".join(parts) if parts else "(no metadata)"


def _log_discovery(
    talent: str,
    platform: str,
    metadata: dict,
    candidates: List[str],
    selected: str,
    confidence: float,
    threshold: float,
) -> None:
    """Step 7 — structured per-platform debug log (talent, platform, metadata,
    top-N Serper URLs, selected URL, confidence)."""
    logger.info(
        "[DISCOVERY] talent=%s | platform=%s\n"
        "    metadata : %s\n"
        "    top%d urls: %s\n"
        "    selected : %s (confidence=%.2f, threshold=%.2f)",
        talent, platform, _metadata_summary(metadata),
        len(candidates), candidates or "[]",
        selected or "NOT_FOUND", confidence, threshold,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  STEPS 2-7 — DISCOVER ONE PLATFORM
# ─────────────────────────────────────────────────────────────────────────────

def discover_platform(
    talent: str,
    platform: str,
    metadata: dict,
    wiki_url: Optional[str] = None,
    threshold: float = PROFILE_MATCH_THRESHOLD,
) -> dict:
    """Run Steps 2-7 for a single platform and return the result object:

        {talent_name, platform, profile_url, source, confidence, reasoning,
         candidate_urls}

    `candidate_urls` is the exact top-N list Serper returned (after the light
    empty/dup/host pruning of Step 4) so callers can inspect what was considered.
    """
    base = {"talent_name": talent, "platform": platform}

    # ── Step 2 — already verified from Wikipedia/Wikidata ──
    if wiki_url:
        logger.info("[%s] %s -> WIKIPEDIA %s", talent, platform, wiki_url)
        return {
            **base,
            "profile_url": wiki_url,
            "source": SOURCE_WIKIPEDIA,
            "confidence": WIKIPEDIA_CONFIDENCE,
            "reasoning": "Verified social profile present in Wikipedia/Wikidata.",
            "candidate_urls": [wiki_url],
        }

    not_found = {
        **base, "profile_url": "", "source": SOURCE_NOT_FOUND,
        "confidence": 0.0, "reasoning": "", "candidate_urls": [],
    }

    try:
        # ── Step 3 — Serper search ──
        query = build_query(talent, platform)
        results = serper_search_with_retry(query)
        logger.info("[%s] %s | '%s' -> %d raw results", talent, platform, query, len(results))

        # ── Step 4 — top-N candidates exactly as returned ──
        candidates = select_candidates(results, platform)
        if not candidates:
            _log_discovery(talent, platform, metadata, candidates, "", 0.0, threshold)
            return {
                **not_found,
                "reasoning": "No candidate URLs returned by search.",
            }

        # ── Step 5 — LLM verification ──
        verdict = llm_verify(metadata, platform, candidates)
        selected = verdict["selected_url"]
        confidence = verdict["confidence"]
        reasoning = verdict["reasoning"]

        # ── Step 7 — debug log ──
        _log_discovery(talent, platform, metadata, candidates, selected, confidence, threshold)

        # ── Step 6 — confidence threshold gate ──
        if selected and confidence >= threshold:
            return {
                **base, "profile_url": selected, "source": SOURCE_LLM,
                "confidence": round(confidence, 4), "reasoning": reasoning,
                "candidate_urls": candidates,
            }
        return {
            **not_found,
            "confidence": round(confidence, 4),
            "reasoning": (
                reasoning
                or f"Below confidence threshold ({confidence:.2f} < {threshold:.2f})."
            ),
            "candidate_urls": candidates,
        }
    except RuntimeError:
        raise  # fatal API error — bubble up to stop the job
    except Exception as exc:  # noqa: BLE001
        logger.error("[%s] %s discovery error: %s", talent, platform, exc)
        return {**not_found, "reasoning": f"Error: {exc}"}


# ─────────────────────────────────────────────────────────────────────────────
#  FULL TALENT — STEPS 1-7
# ─────────────────────────────────────────────────────────────────────────────

def discover_talent(
    talent: str,
    wikipedia_url: str = "",
    title_category: str = "",
    title_sub_category: str = "",
    platforms: Optional[List[str]] = None,
    threshold: float = PROFILE_MATCH_THRESHOLD,
    on_platform: Optional[callable] = None,
) -> Dict[str, dict]:
    """Run the full Wikipedia + Serper + LLM workflow for one talent.

    Returns {platform: result_object}. `on_platform(platform, phase)` is an
    optional progress callback (phase ∈ {"start", "done"}).
    """
    platforms = platforms or TARGET_PLATFORMS

    # Step 1 — metadata
    metadata = extract_wikipedia_metadata(wikipedia_url, talent)
    logger.info("[%s] metadata keys: %s", talent, list(metadata.keys()))

    # Step 2 — existing Wikipedia/Wikidata socials
    wiki_socials = get_wikipedia_socials(
        talent, wikipedia_url, title_category, title_sub_category, platforms
    )

    results: Dict[str, dict] = {}
    for platform in platforms:
        if on_platform:
            on_platform(platform, "start")
        results[platform] = discover_platform(
            talent, platform, metadata, wiki_socials.get(platform), threshold
        )
        if on_platform:
            on_platform(platform, "done")
    return results
