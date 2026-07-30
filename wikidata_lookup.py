"""
wikidata_lookup.py  —  Wikipedia/Wikidata + Official Website social discovery
==============================================================================

Pipeline (runs BEFORE Serper, no API key needed):

  Step 1 — Wikidata structured lookup
    • If wikipedia_url column is present → extract Wikidata QID
    • Query Wikidata for social handles (P2002=X, P2003=IG, P2397=YT, P7085=TT, P2013=FB)
    • Also grab official website (P856) for Step 2
    • Returns handles with confidence=0.97 — most reliable source possible

  Step 2 — Official website social link extraction
    • Crawl official website (from Wikidata or 'official_website' column)
    • Scan footer, header, about page for social media links
    • Returns handles with confidence=0.88

  Step 3 — Wikipedia article body scan (fallback)
    • Parse the Wikipedia article HTML for social links
    • Useful when Wikidata properties are sparse
    • Returns handles with confidence=0.82

For platforms already found by Wikidata, Serper is SKIPPED entirely —
saving credits and removing wrong-person errors.

Usage in testing.py:
    from wikidata_lookup import run_wiki_preflight

    wiki_results = run_wiki_preflight(talent, title_category, title_sub_category,
                                      wikipedia_url=row.get("wikipedia_url"))
    # wiki_results: {"Facebook": ("url", 0.97, "wikidata"), ...}
    # Merge into resolved_links before running Serper
"""

import os
import re
import threading
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, urljoin

import requests

# ── HTTP session with sensible headers ──────────────────────────────────────
# Wikimedia asks bots to identify with a real contact; set WIKI_USER_AGENT in the
# environment to your own. A descriptive/contactable UA is throttled less.
_DEFAULT_UA = (
    "SocialMediaFinder/1.0 (talent social-profile verification tool; "
    "+https://github.com/) python-requests"
)
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": os.environ.get("WIKI_USER_AGENT", "").strip() or _DEFAULT_UA,
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
})
TIMEOUT = 15  # seconds per request

# Cap concurrent Wikipedia/Wikidata calls. Each row makes several (QID lookup,
# entity fetch, label resolution, REST summary); 4 concurrent rows produced a
# burst that Wikimedia rate-limited with HTTP 429. This bounds live calls so the
# per-request retry/backoff below can actually succeed.
_WIKI_MAX_CONCURRENCY = max(1, int(os.environ.get("WIKI_MAX_CONCURRENCY", "3")))
_WIKI_SEM = threading.BoundedSemaphore(_WIKI_MAX_CONCURRENCY)
_WIKI_RETRY_STATUS = {429, 500, 502, 503, 504}

# ── Wikidata social property map ─────────────────────────────────────────────
# property_id → (our_platform_key, url_template)
WIKIDATA_SOCIAL_PROPS: Dict[str, Tuple[str, str]] = {
    "P2002": ("X",         "https://x.com/{}"),
    "P2003": ("Instagram",  "https://www.instagram.com/{}"),
    "P2397": ("YouTube",    "https://www.youtube.com/channel/{}"),
    "P7085": ("TikTok",     "https://www.tiktok.com/@{}"),
    "P2013": ("Facebook",   "https://www.facebook.com/{}"),
    "P4033": ("Mastodon",   "{}"),           # not a target platform but useful for cross-ref
    "P856":  ("_website",   "{}"),           # official website — used for Step 2
    "P18":   ("_image",     "{}"),           # image — skip
}

# YouTube handle property (newer — @handle style)
WIKIDATA_YT_HANDLE_PROP = "P11245"          # YouTube handle (@CBSSports style)

# ── Platform URL patterns for HTML scraping ──────────────────────────────────
SOCIAL_URL_PATTERNS: Dict[str, re.Pattern] = {
    "Facebook":  re.compile(
        r'https?://(?:www\.)?facebook\.com/(?!share|sharer|events|groups|watch|marketplace|gaming)'
        r'([\w.\-]+)/?(?:["\'\s>]|$)',
        re.I
    ),
    "Instagram": re.compile(
        r'https?://(?:www\.)?instagram\.com/([\w.\-]+)/?(?:["\'\s>]|$)',
        re.I
    ),
    "X": re.compile(
        r'https?://(?:www\.)?(?:x|twitter)\.com/(?!share|intent|search|i/)'
        r'([\w.\-]+)/?(?:["\'\s>]|$)',
        re.I
    ),
    "TikTok": re.compile(
        r'https?://(?:www\.)?tiktok\.com/@([\w.\-]+)/?(?:["\'\s>]|$)',
        re.I
    ),
    "YouTube": re.compile(
        r'https?://(?:www\.)?youtube\.com/(?:@|channel/|c/|user/)([\w.\-]+)/?(?:["\'\s>]|$)',
        re.I
    ),
}

# Handles to ignore — system accounts, share buttons etc.
_IGNORE_HANDLES = frozenset({
    "sharer", "share", "intent", "search", "login", "home", "explore",
    "notifications", "messages", "settings", "help", "about", "legal",
    "privacy", "terms", "ads", "business", "marketplace", "watch",
    "gaming", "groups", "events", "pages", "people", "places",
    "hashtag", "tag", "music", "discover", "foryou",
    "youtube", "facebook", "instagram", "twitter", "tiktok", "x",
    "google", "apple", "amazon", "microsoft",
})


# ────────────────────────────────────────────────────────────────────────────
#  HELPER: safe HTTP GET
# ────────────────────────────────────────────────────────────────────────────

def _get(
    url: str,
    params: Optional[dict] = None,
    timeout: int = TIMEOUT,
    retries: int = 4,
    backoff: float = 2.0,
) -> Optional[requests.Response]:
    """
    Rate-limit-aware GET for Wikipedia/Wikidata.

    Retries on 429/5xx and connection errors with exponential backoff (honouring
    a ``Retry-After`` header), and bounds concurrency via ``_WIKI_SEM`` so bursts
    don't trip Wikimedia's rate limiter in the first place. Returns None only
    after retries are exhausted, so a transient 429 no longer degrades a row to
    name-only ground truth.
    """
    for attempt in range(retries + 1):
        try:
            with _WIKI_SEM:  # bound concurrent Wikimedia calls
                resp = _SESSION.get(url, params=params, timeout=timeout, allow_redirects=True)
        except requests.RequestException as exc:
            if attempt < retries:
                time.sleep(min(backoff ** attempt, 30))
                continue
            print(f"  [WIKI] Request failed {url[:80]}: {exc.__class__.__name__}")
            return None

        if resp.status_code in _WIKI_RETRY_STATUS:
            if attempt < retries:
                delay = backoff ** attempt
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        pass
                time.sleep(min(delay, 30))  # released the semaphore before sleeping
                continue
            print(f"  [WIKI] HTTP {resp.status_code} (rate-limited; retries exhausted) {url[:80]}")
            return None

        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError:
            print(f"  [WIKI] HTTP {resp.status_code} fetching {url[:80]}")
            return None
        return resp
    return None


# ────────────────────────────────────────────────────────────────────────────
#  STEP 0: Extract Wikidata QID from a Wikipedia URL
# ────────────────────────────────────────────────────────────────────────────

def _wikipedia_url_to_qid(wikipedia_url: str) -> Optional[str]:
    """
    Convert a Wikipedia article URL to a Wikidata QID (e.g. "Q312").
    Works for any language Wikipedia.

    https://en.wikipedia.org/wiki/Tyler_Bey  →  Q105756072
    """
    wikipedia_url = (wikipedia_url or "").strip()
    if not wikipedia_url:
        return None

    # Direct Wikidata URL? e.g. https://www.wikidata.org/wiki/Q312
    m = re.search(r"wikidata\.org/wiki/(Q\d+)", wikipedia_url, re.I)
    if m:
        return m.group(1)

    # Extract language + title from Wikipedia URL
    m = re.match(r"https?://([a-z\-]+)\.wikipedia\.org/wiki/(.+)", wikipedia_url, re.I)
    if not m:
        print(f"  [WIKI] Cannot parse Wikipedia URL: {wikipedia_url[:80]}")
        return None

    lang  = m.group(1)
    title = m.group(2).split("#")[0]   # strip anchor

    resp = _get(
        f"https://{lang}.wikipedia.org/w/api.php",
        params={
            "action":  "query",
            "titles":  title,
            "prop":    "pageprops",
            "format":  "json",
            "formatversion": "2",
        },
    )
    if not resp:
        return None

    try:
        pages = resp.json()["query"]["pages"]
        for page in pages:
            qid = page.get("pageprops", {}).get("wikibase_item")
            if qid:
                return qid
    except Exception as e:
        print(f"  [WIKI] QID extraction failed: {e}")
    return None


def _name_to_qid(talent: str, title_category: str = "", title_sub_category: str = "") -> Optional[str]:
    """
    When no Wikipedia URL is provided, try to find the Wikidata entity via
    the Wikipedia search API. This is a best-effort fallback.
    """
    # Build a disambiguation-aware search term
    blob = f"{title_category or ''} {title_sub_category or ''}".lower()
    disambig_hints = []
    if "basketball" in blob:                disambig_hints.append("basketball player")
    elif "football" in blob:                disambig_hints.append("American football player")
    elif "athlete" in blob:                 disambig_hints.append("athlete")
    elif re.search(r"publisher|publication|network|tv network", blob):
        disambig_hints.append("media organization")
    elif "musician" in blob or "singer" in blob: disambig_hints.append("musician")
    elif "actor" in blob or "actress" in blob:   disambig_hints.append("actor")

    # Strip parentheticals from talent name for search
    clean_name = re.sub(r"\s*\([^)]+\)\s*", " ", talent).strip()
    search_term = clean_name
    if disambig_hints:
        search_term = f"{clean_name} {disambig_hints[0]}"

    resp = _get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action":   "query",
            "list":     "search",
            "srsearch": search_term,
            "srnamespace": 0,
            "srlimit":  3,
            "format":   "json",
        },
    )
    if not resp:
        return None

    try:
        results = resp.json()["query"]["search"]
        if not results:
            return None
        # Use the first result — re-run with its title to get QID
        top_title = results[0]["title"]
        return _wikipedia_url_to_qid(f"https://en.wikipedia.org/wiki/{top_title.replace(' ', '_')}")
    except Exception as e:
        print(f"  [WIKI] Search-to-QID failed: {e}")
        return None


# ────────────────────────────────────────────────────────────────────────────
#  STEP 1: Wikidata structured social property lookup
# ────────────────────────────────────────────────────────────────────────────

def _fetch_wikidata_entity(qid: str) -> Optional[dict]:
    """Fetch the full Wikidata entity JSON for a QID."""
    resp = _get(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json")
    if not resp:
        return None
    try:
        return resp.json()["entities"][qid]
    except Exception as e:
        print(f"  [WIKI] Wikidata entity fetch failed ({qid}): {e}")
        return None


def _extract_wikidata_socials(entity: dict) -> Dict[str, Tuple[str, str, str]]:
    """
    Extract social media handles from a Wikidata entity's claims.

    Returns: {platform: (url, handle, source_property)}
    """
    claims  = entity.get("claims", {})
    results = {}

    for prop, (platform, url_tpl) in WIKIDATA_SOCIAL_PROPS.items():
        if platform.startswith("_") or prop not in claims:
            continue
        try:
            snak  = claims[prop][0]["mainsnak"]
            if snak.get("snaktype") != "value":
                continue
            value = snak["datavalue"]["value"]
            if isinstance(value, str) and value.strip():
                handle = value.strip().lstrip("@")
                url    = url_tpl.format(handle)
                results[platform] = (url, handle, prop)
        except (KeyError, IndexError, TypeError):
            continue

    # YouTube: prefer @handle (P11245) over channel ID (P2397) when available
    if WIKIDATA_YT_HANDLE_PROP in claims:
        try:
            snak = claims[WIKIDATA_YT_HANDLE_PROP][0]["mainsnak"]
            if snak.get("snaktype") == "value":
                handle = snak["datavalue"]["value"].strip().lstrip("@")
                if handle:
                    results["YouTube"] = (
                        f"https://www.youtube.com/@{handle}",
                        handle,
                        WIKIDATA_YT_HANDLE_PROP,
                    )
        except (KeyError, IndexError, TypeError):
            pass

    # Extract official website separately
    if "P856" in claims:
        try:
            snak = claims["P856"][0]["mainsnak"]
            if snak.get("snaktype") == "value":
                website = snak["datavalue"]["value"].strip()
                if website:
                    results["_website"] = (website, website, "P856")
        except (KeyError, IndexError, TypeError):
            pass

    return results


def lookup_wikidata(
    talent: str,
    title_category: str = "",
    title_sub_category: str = "",
    wikipedia_url: str = "",
) -> Dict[str, Tuple[str, float, str]]:
    """
    Main Wikidata lookup.

    Returns: {platform: (profile_url, confidence, source)}
      confidence = 0.97 for direct Wikidata properties (most reliable source)
    """
    print(f"  [WIKI-STEP1] Wikidata lookup for: {talent}")

    # Get QID — from URL if provided, else search
    qid = None
    if wikipedia_url and wikipedia_url.strip():
        qid = _wikipedia_url_to_qid(wikipedia_url.strip())
        if qid:
            print(f"  [WIKI] QID from URL: {qid}")
    if not qid:
        qid = _name_to_qid(talent, title_category, title_sub_category)
        if qid:
            print(f"  [WIKI] QID from search: {qid}")

    if not qid:
        print(f"  [WIKI] No Wikidata entity found for: {talent}")
        return {}

    entity = _fetch_wikidata_entity(qid)
    if not entity:
        return {}

    socials = _extract_wikidata_socials(entity)

    results: Dict[str, Tuple[str, float, str]] = {}
    for platform, (url, handle, prop) in socials.items():
        if platform.startswith("_"):
            continue
        results[platform] = (url, 0.97, f"wikidata:{prop}(@{handle})")
        print(f"  [WIKI] ✓ {platform:12s} → {url}  (handle=@{handle}, prop={prop})")

    if "_website" in socials:
        website_url = socials["_website"][0]
        results["_website"] = (website_url, 1.0, "wikidata:P856")
        print(f"  [WIKI] Official website: {website_url}")

    return results


# ────────────────────────────────────────────────────────────────────────────
#  STEP 2: Official website social link extraction
# ────────────────────────────────────────────────────────────────────────────

def _extract_socials_from_html(html: str, base_url: str = "") -> Dict[str, List[str]]:
    """
    Scan HTML for social media profile links.
    Returns {platform: [url, url, ...]} — may contain multiple per platform.
    """
    found: Dict[str, List[str]] = {p: [] for p in SOCIAL_URL_PATTERNS}

    for platform, pattern in SOCIAL_URL_PATTERNS.items():
        for m in pattern.finditer(html):
            handle = m.group(1).rstrip("/").strip()
            if not handle or handle.lower() in _IGNORE_HANDLES:
                continue
            if len(handle) < 2 or len(handle) > 50:
                continue
            # Reconstruct clean URL
            if platform == "X":
                url = f"https://x.com/{handle}"
            elif platform == "Facebook":
                url = f"https://www.facebook.com/{handle}"
            elif platform == "Instagram":
                url = f"https://www.instagram.com/{handle}"
            elif platform == "TikTok":
                url = f"https://www.tiktok.com/@{handle}"
            elif platform == "YouTube":
                # Preserve the path type (/@handle, /channel/, /c/, /user/)
                full = m.group(0).split('"')[0].split("'")[0].strip().rstrip("/")
                url  = full if full.startswith("http") else f"https://www.youtube.com/{handle}"
            else:
                continue
            if url not in found[platform]:
                found[platform].append(url)

    return {p: urls for p, urls in found.items() if urls}


def _find_social_links_pages(base_url: str) -> List[str]:
    """Return candidate page URLs on the site most likely to contain social links."""
    base = base_url.rstrip("/")
    return [
        base,
        f"{base}/about",
        f"{base}/about-us",
        f"{base}/contact",
        f"{base}/contact-us",
        f"{base}/links",
    ]


def _crawl_official_website(website_url: str, talent: str) -> Dict[str, Tuple[str, float, str]]:
    """
    Crawl an official website and extract social media links.

    Strategy:
      1. Fetch homepage — social icons are almost always in the footer
      2. If few results, try /about and /contact pages
      3. De-duplicate and pick the most plausible handle per platform
    """
    print(f"  [WIKI-STEP2] Crawling official website: {website_url}")
    collected: Dict[str, List[str]] = {}

    for page_url in _find_social_links_pages(website_url):
        resp = _get(page_url, timeout=12)
        if not resp:
            continue
        html = resp.text or ""

        page_socials = _extract_socials_from_html(html, page_url)
        for platform, urls in page_socials.items():
            if platform not in collected:
                collected[platform] = []
            for u in urls:
                if u not in collected[platform]:
                    collected[platform].append(u)

        # Homepage is enough if we found all 5 platforms
        if page_url == website_url.rstrip("/") and len(collected) >= 4:
            break

        time.sleep(0.3)

    # Pick best URL per platform: prefer handles that contain name tokens
    talent_slug = re.sub(r"[^a-z0-9]", "", talent.lower())
    results: Dict[str, Tuple[str, float, str]] = {}

    for platform, urls in collected.items():
        if not urls:
            continue
        # Score each URL: prefer those whose handle contains talent name tokens
        def _score(u: str) -> float:
            path = urlparse(u).path.lower().replace("/", "").replace("@", "")
            slug = re.sub(r"[^a-z0-9]", "", path)
            score = 0.0
            if talent_slug and len(talent_slug) >= 4 and talent_slug[:6] in slug:
                score += 5.0
            for token in talent.lower().split():
                t = re.sub(r"[^a-z0-9]", "", token)
                if len(t) >= 3 and t in slug:
                    score += 2.0
            return score

        best = max(urls, key=_score)
        results[platform] = (best, 0.88, f"official_website:{website_url[:40]}")
        print(f"  [WIKI] ✓ {platform:12s} → {best}  (from official website)")

    return results


def crawl_official_website(
    website_url: str,
    talent: str,
) -> Dict[str, Tuple[str, float, str]]:
    """Public wrapper with error handling."""
    if not website_url or not website_url.strip():
        return {}
    try:
        return _crawl_official_website(website_url.strip(), talent)
    except Exception as e:
        print(f"  [WIKI] Website crawl error: {e}")
        return {}


# ────────────────────────────────────────────────────────────────────────────
#  STEP 3: Wikipedia article body scan (fallback)
# ────────────────────────────────────────────────────────────────────────────

def _scan_wikipedia_article(wikipedia_url: str, talent: str) -> Dict[str, Tuple[str, float, str]]:
    """
    Fetch the Wikipedia article HTML and scan for social links in:
      • External links section
      • Infobox links
      • Article body
    """
    print(f"  [WIKI-STEP3] Scanning Wikipedia article: {wikipedia_url[:80]}")
    resp = _get(wikipedia_url, timeout=12)
    if not resp:
        return {}

    html = resp.text or ""
    found = _extract_socials_from_html(html, wikipedia_url)

    results: Dict[str, Tuple[str, float, str]] = {}
    for platform, urls in found.items():
        if urls:
            results[platform] = (urls[0], 0.82, "wikipedia_article")
            print(f"  [WIKI] ✓ {platform:12s} → {urls[0]}  (from Wikipedia article)")

    return results


# ────────────────────────────────────────────────────────────────────────────
#  MAIN ENTRY POINT
# ────────────────────────────────────────────────────────────────────────────

def run_wiki_preflight(
    talent: str,
    title_category: str = "",
    title_sub_category: str = "",
    wikipedia_url: str = "",
    official_website: str = "",
    target_platforms: Optional[List[str]] = None,
) -> Dict[str, Tuple[str, float, str]]:
    """
    Run the full Wikipedia/Wikidata preflight pipeline for one talent.

    Returns: {platform: (profile_url, confidence, source)}

    Platforms found here should be merged into resolved_links BEFORE Serper
    runs — skipping Serper entirely for those platforms saves credits and
    removes wrong-person errors.

    Priority order:
      Wikidata (0.97) > Official Website (0.88) > Wikipedia article (0.82)

    Args:
        talent:           Talent/brand name
        title_category:   From Excel (e.g. "Publishers")
        title_sub_category: From Excel (e.g. "Publication Type - Sports")
        wikipedia_url:    From Excel 'wikipedia_url' column (optional)
        official_website: From Excel 'official_website' column (optional)
        target_platforms: Only return results for these platforms
                          (default: Facebook, Instagram, X, TikTok, YouTube)
    """
    if target_platforms is None:
        target_platforms = ["Facebook", "Instagram", "X", "TikTok", "YouTube"]

    results: Dict[str, Tuple[str, float, str]] = {}

    # ── Step 1: Wikidata ──────────────────────────────────────────────────────
    wikidata_results = lookup_wikidata(
        talent, title_category, title_sub_category, wikipedia_url
    )
    # Merge Wikidata results (highest confidence, never overwrite)
    official_website_from_wikidata = ""
    for platform, (url, conf, source) in wikidata_results.items():
        if platform == "_website":
            official_website_from_wikidata = url
            continue
        if platform in target_platforms:
            results[platform] = (url, conf, source)

    # ── Step 2: Official Website ──────────────────────────────────────────────
    # Use provided official_website column first, fall back to Wikidata's P856
    website_to_crawl = (official_website or "").strip() or official_website_from_wikidata
    missing = [p for p in target_platforms if p not in results]

    if website_to_crawl and missing:
        website_results = crawl_official_website(website_to_crawl, talent)
        for platform, (url, conf, source) in website_results.items():
            if platform in missing:   # only fill what Wikidata didn't cover
                results[platform] = (url, conf, source)
        missing = [p for p in target_platforms if p not in results]

    # ── Step 3: Wikipedia article scan (fallback) ─────────────────────────────
    if wikipedia_url and missing:
        wiki_article_results = _scan_wikipedia_article(wikipedia_url, talent)
        for platform, (url, conf, source) in wiki_article_results.items():
            if platform in missing:
                results[platform] = (url, conf, source)

    # ── Summary ───────────────────────────────────────────────────────────────
    found_count = len(results)
    total       = len(target_platforms)
    print(
        f"  [WIKI] Preflight done for '{talent}': "
        f"{found_count}/{total} platforms found "
        f"({', '.join(results.keys()) or 'none'})"
    )
    if found_count < total:
        still_missing = [p for p in target_platforms if p not in results]
        print(f"  [WIKI] Still needs Serper: {still_missing}")

    return results