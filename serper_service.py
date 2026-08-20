"""
serper_service.py  —  Serper.dev search (context extraction + backup discovery)
================================================================================

Serper has two responsibilities in the workflow:

  Part A — CONTEXT EXTRACTION for an existing link (e.g. an Apify result):
      ``context_for_url(url)`` searches the exact profile URL and returns ALL
      metadata Serper exposes from the top results (title, snippet, displayed
      link, knowledge graph, follower/subscriber/like counts, dates, sitelinks).

  Part B — BACKUP DISCOVERY for a platform Apify could not resolve:
      ``discover_candidates(talent, platform, identifiers)`` issues a
      metadata-rich natural-language query and returns the top profile-URL
      candidates, each carrying the same rich Serper metadata.

Environment variables:
    SERPER_API_KEY   Serper.dev API key
"""

from __future__ import annotations

import copy
import os
import re
import threading
import time
from typing import Any, Dict, List
from urllib.parse import urlparse

import requests

import social_urls
from retry_util import request_with_retry

SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "").strip()

_SERPER_URL = "https://google.serper.dev/search"

# Cap concurrent Serper calls. The pipeline can launch ~20 at once (rows ×
# platforms); that burst of simultaneous TLS handshakes caused SSL connection
# resets. This bounds live Serper connections regardless of row/platform workers.
_SERPER_MAX_CONCURRENCY = max(1, int(os.environ.get("SERPER_MAX_CONCURRENCY", "6")))
_SERPER_SEM = threading.BoundedSemaphore(_SERPER_MAX_CONCURRENCY)

# Natural-language platform term used when building discovery queries.
_PLATFORM_TERM: Dict[str, str] = {
    "Instagram": "Instagram",
    "Facebook": "Facebook",
    "YouTube": "YouTube",
    "TikTok": "TikTok",
    "X": "Twitter X",
}

_COUNT_RE = re.compile(
    r"([\d][\d.,]*\s*[KMB]?)\+?\s+(followers|subscribers|likes|following|fans)",
    re.I,
)


# Per-run cache so the same profile URL isn't looked up on Serper twice.
_CONTEXT_CACHE: Dict[str, Dict[str, Any]] = {}

# Per-run cache of site-search DISCOVERY results, keyed by (talent, platform).
# Google does not return a stable result set for repeated identical queries, so
# without this the same talent appearing twice in a file (different brand_id,
# same person) gets different candidates and therefore different labels — one
# row Verified, the other Manual Review. Caching makes a run reproducible and
# halves the Serper spend on duplicate rows.
_DISCOVERY_CACHE: Dict[tuple, List[dict]] = {}
_DISCOVERY_LOCK = threading.Lock()


def clear_cache() -> None:
    _CONTEXT_CACHE.clear()
    with _DISCOVERY_LOCK:
        _DISCOVERY_CACHE.clear()


def is_configured() -> bool:
    return bool(SERPER_API_KEY)


# ────────────────────────────────────────────────────────────────────────────
#  Low-level search
# ────────────────────────────────────────────────────────────────────────────

def serper_search_raw(query: str, num_results: int = 10) -> dict:
    """Full Serper JSON (organic + knowledgeGraph + …). Raises on fatal errors."""
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload = {"q": query, "num": max(1, min(num_results, 10))}
    with _SERPER_SEM:  # bound concurrent Serper connections (avoids TLS-burst resets)
        response = request_with_retry("POST", _SERPER_URL, headers=headers, json=payload, timeout=30)
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        try:
            detail = response.json().get("message") or response.text
        except ValueError:
            detail = response.text
        raise RuntimeError(f"Serper search failed ({response.status_code}): {detail}") from exc
    return response.json()


def _parse_counts(text: str) -> Dict[str, str]:
    """Extract follower/subscriber/like/following counts from free text."""
    counts: Dict[str, str] = {}
    for value, label in _COUNT_RE.findall(text or ""):
        key = label.lower()
        counts.setdefault(key, value.strip())
    return counts


def _displayed_link(item: dict) -> str:
    link = item.get("link", "") or ""
    displayed = item.get("displayedLink") or item.get("displayed_link")
    if displayed:
        return displayed
    try:
        return urlparse(link).netloc
    except Exception:
        return ""


def _organic_fields(item: dict) -> Dict[str, Any]:
    """Extract every useful field from one organic result."""
    fields: Dict[str, Any] = {
        "title": item.get("title", "") or "",
        "snippet": item.get("snippet", "") or "",
        "link": item.get("link", "") or "",
        "displayed_link": _displayed_link(item),
    }
    if item.get("date"):
        fields["date"] = item["date"]
    if item.get("position") is not None:
        fields["position"] = item["position"]
    sitelinks = item.get("sitelinks") or []
    if sitelinks:
        fields["sitelinks"] = [s.get("title", "") for s in sitelinks if s.get("title")]
    if item.get("attributes"):
        fields["attributes"] = item["attributes"]
    return fields


def _knowledge_graph(data: dict) -> Dict[str, Any]:
    kg = data.get("knowledgeGraph") or {}
    if not kg:
        return {}
    out: Dict[str, Any] = {}
    for key in ("title", "type", "description", "website", "imageUrl"):
        if kg.get(key):
            out[key] = kg[key]
    if kg.get("attributes"):
        out["attributes"] = kg["attributes"]
    return out


# ────────────────────────────────────────────────────────────────────────────
#  Part A — context extraction for an existing URL
# ────────────────────────────────────────────────────────────────────────────

def context_for_url(url: str, top_results: int = 2) -> Dict[str, Any]:
    """
    Search the exact profile URL and return all metadata from the top results.
    Returns ``{}`` on failure so the caller can proceed with what it has.
    """
    if not is_configured() or not url:
        return {}
    if url in _CONTEXT_CACHE:
        return _CONTEXT_CACHE[url]
    try:
        data = serper_search_raw(url, num_results=max(top_results, 5))
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"  [SERPER] context fetch failed {url[:60]}… : {exc.__class__.__name__}")
        return {}

    organic = data.get("organic", []) or []
    results = [_organic_fields(item) for item in organic[:top_results]]
    kg = _knowledge_graph(data)

    context: Dict[str, Any] = {}
    if results:
        context["serper_title"] = results[0]["title"]
        context["serper_snippet"] = results[0]["snippet"]
        context["serper_results"] = results
    if kg:
        context["knowledge_graph"] = kg

    # Parse counts from everything we gathered.
    blob = " ".join(
        [r.get("title", "") + " " + r.get("snippet", "") for r in results]
        + [str(kg.get("attributes", "")), kg.get("description", "")]
    )
    context.update(_parse_counts(blob))
    _CONTEXT_CACHE[url] = context
    return context


# ────────────────────────────────────────────────────────────────────────────
#  Part B — backup discovery for a missing platform
# ────────────────────────────────────────────────────────────────────────────

def discover_candidates(
    talent: str,
    platform: str,
    identifiers: str = "",
    top_n: int = 3,
) -> List[dict]:
    """
    Metadata-rich natural-language search for a platform's profile.

    ``identifiers`` is a short string of distinguishing facts (occupation, team,
    sport, nationality, …) that the pipeline builds from the ground-truth
    metadata. Returns up to ``top_n`` candidates shaped like
    ``{url, source, meta{...}}`` with the full Serper metadata attached.
    """
    if not is_configured():
        print("  [SERPER] Skipped — SERPER_API_KEY not set.")
        return []

    term = _PLATFORM_TERM.get(platform, platform)
    ident = (identifiers or "").strip()
    queries = [q for q in (
        f"{talent} {ident} {term}".strip(),
        f"{talent} official {term}",
    ) if q]

    candidates: List[dict] = []
    seen: set = set()
    for query in queries:
        try:
            data = serper_search_raw(query, num_results=10)
        except RuntimeError as exc:
            print(f"  [SERPER] Fatal error on '{query}': {exc}")
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"  [SERPER] Query failed '{query}': {exc}")
            continue

        for item in data.get("organic", []) or []:
            link = item.get("link", "")
            if social_urls.platform_from_url(link) != platform:
                continue
            if not social_urls.is_valid_profile_url(link, platform):
                continue
            norm = social_urls.normalize_profile_url(link, platform)
            if norm in seen:
                continue
            seen.add(norm)
            meta = _organic_fields(item)
            meta.update(_parse_counts(meta.get("title", "") + " " + meta.get("snippet", "")))
            candidates.append({"url": norm, "source": "serper", "meta": meta})
            if len(candidates) >= top_n:
                break
        if len(candidates) >= top_n:
            break
        time.sleep(0.2)

    print(f"  [SERPER] {platform} | '{talent}' -> {len(candidates)} candidate(s)")
    return candidates


# Placeholders an analyst may use in a custom query template. Anything else in
# the template is passed through to Google verbatim.
QUERY_PLACEHOLDERS = ("name", "platform", "domain", "category", "subcategory")

# The default, used whenever no custom template is supplied. Keeping it here as
# a literal means the Wikipedia flow and the custom flow run the same code path
# with different templates — no separate branch to drift out of sync.
DEFAULT_QUERY_TEMPLATE = "{name} site:{domain}"

# The placeholders an analyst may use. Single source of truth: build_query
# renders exactly these, and search_options.validate_template rejects anything
# else before a run starts.
TEMPLATE_FIELDS = ("name", "platform", "domain", "category", "subcategory")


def build_query(template: str, talent: str, platform: str, domain: str,
                category: str = "", subcategory: str = "") -> str:
    """
    Render a search query from a template.

    Unknown placeholders are left as literal text rather than raising, so a
    template that slipped through validation degrades one search instead of
    crashing a run mid-file. ``search_options.validate_template`` is what stops
    a typo reaching this point at all. Collapses whitespace so that an empty
    category doesn't leave a double space in the query.
    """
    values = {
        "name": talent,
        "platform": platform,
        "domain": domain,
        "category": (category or "").strip(),
        "subcategory": (subcategory or "").strip(),
    }
    assert set(values) == set(TEMPLATE_FIELDS)   # keep validation in step
    out = template or DEFAULT_QUERY_TEMPLATE
    for key, value in values.items():
        out = out.replace("{" + key + "}", value)
    return re.sub(r"\s+", " ", out).strip()


def discover_by_site(talent: str, platform: str, top_n: int = 1,
                     query_template: str = "", category: str = "",
                     subcategory: str = "") -> List[dict]:
    """
    Simplified discovery for entities with NO Wikipedia link in the input.

    Runs a targeted ``"<name> site:<platform-domain>"`` search (e.g.
    ``"Valentino Beauty site:instagram.com"``) and returns the TOP profile-URL
    result(s) only, each shaped like :func:`discover_candidates` output
    (``{url, source, meta{...}}``) with the full Serper metadata attached.
    """
    if not is_configured():
        print("  [SERPER] Skipped site-search — SERPER_API_KEY not set.")
        return []

    domains = social_urls.PLATFORMS.get(platform, [])
    domain = domains[0] if domains else ""
    if not domain:
        return []

    # Deep-copy on the way out: callers mutate candidate meta during enrichment,
    # so handing out the cached objects would leak one row's evidence into another.
    query = build_query(query_template or DEFAULT_QUERY_TEMPLATE, talent,
                        platform, domain, category, subcategory)
    # The template is part of the key: a custom query and the default query are
    # different searches and must not share a cache entry.
    cache_key = (talent.strip().lower(), platform, top_n, query)
    with _DISCOVERY_LOCK:
        cached = _DISCOVERY_CACHE.get(cache_key)
    if cached is not None:
        print(f"  [SERPER] site-search {platform} | '{talent}' -> {len(cached)} candidate(s) (cached)")
        return copy.deepcopy(cached)

    try:
        data = serper_search_raw(query, num_results=10)
    except RuntimeError as exc:
        print(f"  [SERPER] Fatal error on site-search '{query}': {exc}")
        raise
    except requests.RequestException as exc:
        # Connection/SSL/timeout after retries — surface as an error, NOT an
        # empty result, so the pipeline records "errored" rather than "not found".
        print(f"  [SERPER] site-search connection error '{query}': {exc.__class__.__name__}")
        raise RuntimeError(f"Serper connection error: {exc.__class__.__name__}") from exc
    except Exception as exc:  # noqa: BLE001
        print(f"  [SERPER] site-search failed '{query}': {exc}")
        return []

    # The knowledge-graph panel + follower/subscriber counts are ALREADY in this
    # same response — attach them to the chosen candidate (no extra API call) so
    # the LLM gets real identity evidence instead of just a one-line snippet.
    organic = data.get("organic", []) or []
    kg = _knowledge_graph(data)
    blob = " ".join(
        [(it.get("title", "") + " " + it.get("snippet", "")) for it in organic[:5]]
        + [str(kg.get("attributes", "")), kg.get("description", "")]
    )
    broad_counts = _parse_counts(blob)

    candidates: List[dict] = []
    seen: set = set()
    for item in organic:
        link = item.get("link", "")
        if social_urls.platform_from_url(link) != platform:
            continue
        if not social_urls.is_valid_profile_url(link, platform):
            continue
        norm = social_urls.normalize_profile_url(link, platform)
        if norm in seen:
            continue
        seen.add(norm)
        meta = _organic_fields(item)
        # Counts from this result's own text win; fill the rest from the wider
        # response, and attach the knowledge graph so the LLM can confirm identity.
        counts = dict(broad_counts)
        counts.update(_parse_counts(meta.get("title", "") + " " + meta.get("snippet", "")))
        meta.update(counts)
        if kg:
            meta["knowledge_graph"] = kg
        candidates.append({"url": norm, "source": "serper", "meta": meta})
        if len(candidates) >= top_n:
            break

    with _DISCOVERY_LOCK:
        _DISCOVERY_CACHE[cache_key] = copy.deepcopy(candidates)
    print(f"  [SERPER] site-search {platform} | '{talent}' -> {len(candidates)} candidate(s)")
    return candidates
