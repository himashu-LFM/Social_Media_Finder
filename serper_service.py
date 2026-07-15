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

import os
import re
import time
from typing import Any, Dict, List
from urllib.parse import urlparse

import requests

import social_urls

SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "").strip()

_SERPER_URL = "https://google.serper.dev/search"

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


def is_configured() -> bool:
    return bool(SERPER_API_KEY)


# ────────────────────────────────────────────────────────────────────────────
#  Low-level search
# ────────────────────────────────────────────────────────────────────────────

def serper_search_raw(query: str, num_results: int = 10) -> dict:
    """Full Serper JSON (organic + knowledgeGraph + …). Raises on fatal errors."""
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
