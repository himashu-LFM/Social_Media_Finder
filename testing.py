import json
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import pandas as pd
import requests

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

# ================== API KEYS (set in .env: SERPER_API_KEY, OPENAI_API_KEY) ==================
SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()

OPENAI_CHAT_MODEL = os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")

# Optional: load names from Excel in same folder as this script
TEST_BRANDS_PATH = Path(__file__).resolve().parent / "Demo_Social.xlsx"

# ================== INPUT (used if Demo_Social.xlsx is missing) ==================
talent_names = [
    "Britney Vest",
    "Ari Melber",
    "Alyssa Anderson",
    "Andrea",
    "Anastasia Pagonis",
]

# ================== CONFIG ==================
RESULTS_PER_QUERY = 10
MAX_CANDIDATES_FOR_AI = 5
MAX_WORKERS = 3
REQUEST_DELAY_BETWEEN_TALENTS = (1.0, 2.0)
OPENAI_DELAY_SECONDS = 0.4

# Emit a profile URL only if confidence is at least this (otherwise leave cell blank).
MIN_CONFIDENCE_EMIT = float(os.environ.get("MIN_CONFIDENCE_EMIT", "0.72"))
# Use a high-confidence profile page to discover other platforms (bio / Linktree / about).
ANCHOR_MIN_CONFIDENCE = float(os.environ.get("ANCHOR_MIN_CONFIDENCE", "0.86"))
# Only use deterministic fallback (first ranked candidate) if rank score is very strong.
MIN_RANK_SCORE_FOR_FALLBACK = float(os.environ.get("MIN_RANK_SCORE_FOR_FALLBACK", "12.0"))

FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Per-row per-platform confidence (filled in process_row) for enrichment step.
ROW_PLATFORM_CONFIDENCE: Dict[object, Dict[str, float]] = {}
# Per-row per-platform provenance: "input" | "search" | "bio_enrich"
ROW_PLATFORM_SOURCE: Dict[object, Dict[str, str]] = {}

PLATFORMS: Dict[str, List[str]] = {
    "Facebook": ["facebook.com"],
    "Instagram": ["instagram.com"],
    "X": ["x.com", "twitter.com"],
    "TikTok": ["tiktok.com"],
    "YouTube": ["youtube.com"],
}

PLATFORM_CONF_COLUMNS: Dict[str, str] = {
    p: f"{p} Confidence" for p in PLATFORMS
}


def is_first_name_only(talent: str) -> bool:
    parts = re.sub(r"\s+", " ", (talent or "").strip()).split()
    return len(parts) == 1 and bool(parts[0])


def _find_column(raw: pd.DataFrame, *candidates: str) -> Optional[str]:
    cmap = {str(c).strip().lower(): c for c in raw.columns}
    for cand in candidates:
        if cand.lower() in cmap:
            return cmap[cand.lower()]
    return None


def extract_search_keywords(title_category: str, title_sub_category: str) -> str:
    """
    Turn category + sub_category into a short phrase for search queries and ranking.
    Strips noisy labels like 'Talent Type -' so queries stay focused.
    """
    parts: List[str] = []
    for raw in (title_category, title_sub_category):
        if raw is None or (isinstance(raw, float) and pd.isna(raw)):
            continue
        s = str(raw).strip()
        if not s or s.lower() == "nan":
            continue
        parts.append(s)
    if not parts:
        return ""
    text = " ".join(parts)
    text = text.replace(",", " ").replace("|", " ")
    text = re.sub(r"[\r\n\t]+", " ", text)
    # Remove repeated label prefixes (keeps e.g. Basketball, Football, Musician)
    text = re.sub(
        r"(?i)\b(talent type|gender|talent subtype|publication type)\s*-\s*",
        " ",
        text,
    )
    text = re.sub(r"\s+", " ", text).strip()
    # Cap length so Serper queries stay readable
    words = text.split()
    text = " ".join(words[:14])[:140].strip()
    return text


def _default_talent_table() -> pd.DataFrame:
    n = len(talent_names)
    data: Dict[str, List] = {
        "Talent Name": list(talent_names),
        "title_category": [""] * n,
        "title_sub_category": [""] * n,
    }
    for p in PLATFORMS:
        data[p] = [""] * n
    for c in PLATFORM_CONF_COLUMNS.values():
        data[c] = [float("nan")] * n
    data["Confidence"] = [float("nan")] * n
    data["Source"] = [""] * n
    return pd.DataFrame(data)


def load_talent_table_from_path(excel_path: Path) -> pd.DataFrame:
    """
    Load Talent Name + optional title_category / title_sub_category from an .xlsx/.xls file.
    Raises ValueError if the file is unreadable or contains no valid names.
    """
    excel_path = Path(excel_path)
    if not excel_path.is_file():
        raise ValueError(f"File not found: {excel_path}")

    suffix = excel_path.suffix.lower()
    try:
        if suffix == ".csv":
            raw = pd.read_csv(excel_path)
        else:
            raw = pd.read_excel(excel_path)
    except Exception as exc:
        raise ValueError(f"Could not read spreadsheet: {exc}") from exc

    if raw.empty:
        raise ValueError("The file has no rows.")

    name_col = _find_column(raw, "Talent Name", "Talent", "title", "Title", "Name")
    if name_col is None:
        name_col = raw.columns[0]

    cat_col = _find_column(
        raw,
        "title_category",
        "de_category",
        "category",
        "Title Category",
    )
    sub_col = _find_column(raw, "title_sub_category", "sub_category", "Title Sub Category", "subtitle")

    names_list: List[str] = []
    cat_list: List[str] = []
    sub_list: List[str] = []

    for i in range(len(raw)):
        name = str(raw.iloc[i][name_col]).strip()
        if not name or name.lower() == "nan":
            continue
        names_list.append(name)
        c = raw.iloc[i][cat_col] if cat_col else ""
        s = raw.iloc[i][sub_col] if sub_col else ""
        cat_list.append("" if pd.isna(c) else str(c).strip())
        sub_list.append("" if pd.isna(s) else str(s).strip())

    if not names_list:
        raise ValueError("No valid talent names found (need a Talent Name column or data in the first column).")

    n = len(names_list)
    out: Dict[str, List] = {
        "Talent Name": names_list,
        "title_category": cat_list,
        "title_sub_category": sub_list,
    }
    for p in PLATFORMS:
        out[p] = [""] * n
    for c in PLATFORM_CONF_COLUMNS.values():
        out[c] = [float("nan")] * n
    out["Confidence"] = [float("nan")] * n
    out["Source"] = [""] * n
    return pd.DataFrame(out)


def load_talent_table() -> pd.DataFrame:
    """Load Talent Name + optional title_category / title_sub_category from Excel or defaults."""
    if not TEST_BRANDS_PATH.exists():
        return _default_talent_table()

    try:
        return load_talent_table_from_path(TEST_BRANDS_PATH)
    except ValueError as exc:
        print(f"[WARN] {exc}. Using default talent_names.")
        return _default_talent_table()


def build_talent_df(names: List[str], platforms: List[str]) -> pd.DataFrame:
    """Legacy helper: names only, no metadata columns."""
    talent_data: Dict[str, List] = {"Talent Name": names}
    for platform in platforms:
        talent_data[platform] = [""] * len(names)
    for platform in platforms:
        talent_data[f"{platform} Confidence"] = [float("nan")] * len(names)
    talent_data["title_category"] = [""] * len(names)
    talent_data["title_sub_category"] = [""] * len(names)
    talent_data["Confidence"] = [float("nan")] * len(names)
    talent_data["Source"] = [""] * len(names)
    return pd.DataFrame(talent_data)


def build_queries(
    talent: str,
    platform: str,
    domains: List[str],
    search_keywords: str,
    title_category: str = "",
    title_sub_category: str = "",
) -> List[str]:
    """
    Build Serper queries. When search_keywords is non-empty (from title_category +
    title_sub_category), add disambiguated queries so results match the right entity.
    """
    kw = (search_keywords or "").strip()
    queries: List[str] = []
    exp = parse_entity_expectations(title_category, title_sub_category)

    # Sport-first queries when Excel says basketball / male athlete (reduces realtor namesakes)
    if exp["expects_male"] and exp["expects_basketball"]:
        for domain in domains:
            queries.append(f'site:{domain} "{talent}" basketball')
            queries.append(f'site:{domain} "{talent}" basketball player')
            queries.append(f'site:{domain} "{talent}" NCAA basketball')

    for domain in domains:
        queries.append(f'site:{domain} "{talent}" official')
        queries.append(f'site:{domain} "{talent}" verified')
        queries.append(f'site:{domain} "{talent}"')
        if kw:
            queries.append(f'site:{domain} "{talent}" {kw} official')
            queries.append(f'site:{domain} "{talent}" {kw}')

    queries.append(f'"{talent}" {platform} official')
    queries.append(f'"{talent}" {platform}')
    if kw:
        queries.append(f'"{talent}" {kw} {platform} official')
        queries.append(f'"{talent}" {kw} {platform}')

    # De-dupe while preserving order
    seen: set[str] = set()
    unique: List[str] = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            unique.append(q)
    return unique


def is_valid_profile_url(link: str, platform: str) -> bool:
    """
    Return True only for profile/channel URLs, not posts, videos, reels, etc.
    """
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
        if any(seg in full for seg in ("/posts/", "/photos/", "/videos/", "/watch/", "/reel", "/story.php", "/permalink/")):
            return False
        if "profile.php" in path or "/people/" in path or "/pages/" in path:
            return True
        segs = [s for s in path.strip("/").split("/") if s]
        if len(segs) == 1 and segs[0] not in ("share", "sharer", "groups", "events", "marketplace", "gaming", "watch"):
            return True
        return False

    if platform == "Instagram":
        if "instagram.com" not in host:
            return False
        if any(x in full for x in ("/p/", "/reel", "/reels/", "/stories/", "/tv/", "/explore/", "/tags/", "/locations/")):
            return False
        segs = [s for s in path.strip("/").split("/") if s]
        if len(segs) == 1:
            return True
        return False

    if platform == "YouTube":
        if "youtube.com" not in host and "youtu.be" not in host:
            return False
        if any(x in full for x in ("/watch", "/shorts/", "/playlist", "/results", "/live/", "/feed/", "/attribution_link")):
            return False
        if "/@" in full or "/channel/" in full or "/c/" in full or "/user/" in full:
            return True
        return False

    if platform == "X":
        if "x.com" not in host and "twitter.com" not in host:
            return False
        if "/status/" in full or "/i/" in full or "/intent/" in full or "/search" in full:
            return False
        segs = [s for s in path.strip("/").split("/") if s]
        if len(segs) == 1:
            return True
        return False

    if platform == "TikTok":
        if "tiktok.com" not in host:
            return False
        if any(x in full for x in ("/video/", "/tag/", "/music/", "/discover", "/foryou")):
            return False
        if re.search(r"tiktok\.com/@[^/]+/?$", full):
            return True
        return False

    return False


def candidate_rank_score(
    talent: str,
    c: dict,
    search_keywords: str,
    title_category: str = "",
    title_sub_category: str = "",
) -> float:
    title = (c.get("title") or "").lower()
    snippet = (c.get("snippet") or "").lower()
    link = (c.get("link") or "").lower()
    t = re.sub(r"\s+", " ", (talent or "").strip()).lower()
    score = 0.0
    if "official" in title or "official" in snippet:
        score += 4.0
    if "verified" in title or "verified" in snippet or "✓" in (c.get("title") or ""):
        score += 3.0
    if t and t in title:
        score += 3.0
    if t and t in snippet:
        score += 2.0
    if t and t in link.replace("-", " "):
        score += 1.0
    # Metadata alignment (e.g. basketball, sports publisher)
    for token in _metadata_tokens(search_keywords):
        if len(token) < 3:
            continue
        if token in title or token in snippet:
            score += 1.5
    rej, _ = entity_profile_rejected(talent, title_category, title_sub_category, c)
    if rej:
        score -= 35.0
    return score


def _metadata_tokens(search_keywords: str) -> List[str]:
    if not search_keywords:
        return []
    parts = re.split(r"[^\w]+", search_keywords.lower())
    stop = {
        "the",
        "and",
        "for",
        "type",
        "talent",
        "gender",
        "subtype",
        "publication",
        "network",
    }
    # Keep man/woman/basketball/athlete for ranking when present in keywords
    return [p for p in parts if p and p not in stop and len(p) > 2]


def parse_entity_expectations(title_category: str, title_sub_category: str) -> Dict[str, bool]:
    """Structured signals from Excel category + subcategory (e.g. Gender - Man, Athlete - Basketball)."""
    blob = f"{title_category or ''} {title_sub_category or ''}".lower()
    return {
        "expects_male": bool(re.search(r"gender\s*-\s*man\b", blob)),
        "expects_female": bool(re.search(r"gender\s*-\s*woman\b", blob)),
        "expects_athlete": bool(
            re.search(r"\bathlete\b", blob)
            or re.search(r"\bbasketball\b", blob)
            or re.search(r"\bfootball\b", blob)
            or "sport" in blob
        ),
        "expects_basketball": "basketball" in blob,
    }


def entity_profile_rejected(
    talent: str,
    title_category: str,
    title_sub_category: str,
    candidate: Optional[dict],
) -> Tuple[bool, str]:
    """
    Reject Serper candidates that clearly contradict Excel metadata (wrong industry/person).
    Prefer blank cells over wrong Facebook/etc. links for namesakes.
    """
    if not candidate:
        return False, ""
    title = (candidate.get("title") or "")
    snippet = (candidate.get("snippet") or "")
    blob = f"{title} {snippet}".lower()
    exp = parse_entity_expectations(title_category, title_sub_category)

    sport_markers = (
        "basketball",
        "nba",
        "wnba",
        "ncaa",
        "college basketball",
        "draft",
        "athlete",
        "espn",
        "sport",
        "point guard",
        "shooting guard",
        "forward",
        "center",
        "hoops",
        "nba draft",
    )
    sport_hit = any(m in blob for m in sport_markers)

    non_sport_professions = (
        "realtor",
        "real estate",
        "mortgage",
        "homes realty",
        "florida homes",
        "digital creator",
        "realtor sales",
        "realty & mortgage",
        "realty and mortgage",
        "listing agent",
    )
    non_sport_hit = any(m in blob for m in non_sport_professions)

    # Male + athlete (esp. basketball): do not accept obvious realtor / unrelated creator pages
    if exp["expects_male"] and (exp["expects_athlete"] or exp["expects_basketball"]):
        if non_sport_hit and not sport_hit:
            return (
                True,
                "Metadata indicates a male athlete; this result looks like realtor/creator/real estate, not sports.",
            )
        # Common female first names in title/snippet when we expect a man + athlete (namesake)
        female_name_hits = (
            "bobbie ",
            " bobbie",
            "brittany ",
            "britney ",
            "jessica ",
            "samantha ",
            "miss ",
            " mrs ",
        )
        if any(x in blob for x in female_name_hits) and not sport_hit:
            return (
                True,
                "Profile text suggests a different person (female-leaning name/role) vs Gender-Man athlete metadata.",
            )

    if exp["expects_female"] and exp["expects_athlete"] and non_sport_hit and not sport_hit:
        male_lean = (" mr ", "his ", "his own", "father", "husband")
        if any(x in blob for x in male_lean) and "woman" not in blob:
            return True, "Metadata indicates female athlete; result looks unrelated (non-sports professional)."

    return False, ""


def sort_candidates_for_ai(
    talent: str,
    candidates: List[dict],
    search_keywords: str,
    title_category: str = "",
    title_sub_category: str = "",
) -> List[dict]:
    return sorted(
        candidates,
        key=lambda c: -candidate_rank_score(
            talent, c, search_keywords, title_category, title_sub_category
        ),
    )


def first_valid_profile_link(candidates: List[dict], platform: str) -> str:
    for item in candidates:
        link = item.get("link", "")
        if is_valid_profile_url(link, platform):
            return normalize_profile_url(link, platform)
    return ""


def normalize_profile_url(url: str, platform: str) -> str:
    """Normalize host (e.g. mobile YouTube) for consistent output."""
    if not url or not isinstance(url, str):
        return ""
    u = url.strip()
    if platform == "YouTube":
        u = u.replace("://m.youtube.com", "://www.youtube.com")
        u = u.replace("://music.youtube.com", "://www.youtube.com")
        if "youtube.com" in u and "www." not in urlparse(u).netloc and "m." not in urlparse(u).netloc:
            u = u.replace("://youtube.com", "://www.youtube.com")
    return u.rstrip("/")


def _slug_chars(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def talent_url_aligned(talent: str, link: str) -> bool:
    """Heuristic: name tokens appear in profile URL path (reduces wrong person)."""
    t = _slug_chars(talent)
    if len(t) < 4:
        return False
    path = urlparse(link).path
    path_compact = _slug_chars(path)
    if len(t) >= 6 and t[: min(8, len(t))] in path_compact:
        return True
    for part in re.sub(r"\s+", " ", (talent or "").strip()).lower().split():
        if len(part) < 3:
            continue
        sp = _slug_chars(part)
        if len(sp) >= 5 and sp in path_compact:
            return True
    return False


def decide_emitted_link(
    talent: str,
    platform: str,
    selected: str,
    confidence: float,
    reason: str,
    top_candidate: Optional[dict],
    search_keywords: str,
    title_category: str = "",
    title_sub_category: str = "",
    emit_candidate: Optional[dict] = None,
) -> Tuple[str, float, str]:
    """Prefer blank cells over wrong links when confidence is low."""
    if not selected or selected == "Not Found":
        return "", confidence, reason or "No selection."

    selected = normalize_profile_url(selected, platform)
    if not is_valid_profile_url(selected, platform):
        return "", 0.0, "Rejected: not a valid profile/channel URL."

    if emit_candidate:
        rej, why = entity_profile_rejected(
            talent, title_category, title_sub_category, emit_candidate
        )
        if rej:
            return "", min(confidence, 0.12), why

    if confidence >= MIN_CONFIDENCE_EMIT:
        return selected, confidence, reason

    if top_candidate is not None:
        rej_fb, rej_msg = entity_profile_rejected(
            talent, title_category, title_sub_category, top_candidate
        )
        if rej_fb:
            return "", confidence, f"Omitted: {rej_msg}"
        rs = candidate_rank_score(
            talent, top_candidate, search_keywords, title_category, title_sub_category
        )
        link = top_candidate.get("link", "")
        if (
            rs >= MIN_RANK_SCORE_FOR_FALLBACK
            and talent_url_aligned(talent, link)
            and is_valid_profile_url(link, platform)
        ):
            return (
                normalize_profile_url(link, platform),
                min(confidence, 0.68),
                f"Strong search rank + URL match ({rs:.1f}): {reason}",
            )

    return "", confidence, f"Omitted (below {MIN_CONFIDENCE_EMIT:.2f}): {reason}"


URL_IN_TEXT_RE = re.compile(r"https?://[^\s\"\'<>\)\]]+", re.I)


def fetch_html(url: str) -> str:
    try:
        r = requests.get(url, headers=FETCH_HEADERS, timeout=20, allow_redirects=True)
        r.raise_for_status()
        if len(r.content) > 2_500_000:
            return ""
        return r.text or ""
    except Exception as exc:
        print(f"[WARN] fetch failed {url[:90]}… : {exc}")
        return ""


def extract_urls_from_html(html: str) -> List[str]:
    if not html:
        return []
    found: set[str] = set()
    for m in URL_IN_TEXT_RE.finditer(html):
        u = m.group(0).rstrip(".,);\\]}\"'")
        if u.startswith("http"):
            found.add(u.split("&utm_")[0])
    for m in re.finditer(r'href\s*=\s*["\']([^"\']+)["\']', html, re.I):
        h = m.group(1).strip()
        if h.startswith("http"):
            found.add(h.split("&utm_")[0])
    return list(found)


def _platform_for_discovered_url(url: str) -> Optional[str]:
    u = url.lower()
    for plat in PLATFORMS:
        if is_valid_profile_url(url, plat):
            return plat
    if "linktr.ee/" in u or "linktree.com/" in u or "lnk.bio" in u or "beacons.ai" in u:
        return "__link_hub__"
    return None


def extract_social_links_from_page(page_url: str, source_platform: str) -> Dict[str, str]:
    """
    Pull external profile URLs from a public page (bio, about, or link-in-bio services).
    Instagram often blocks scraping; YouTube /about and Linktree work more reliably.
    """
    out: Dict[str, str] = {}
    to_fetch: List[str] = [page_url]

    if source_platform == "YouTube":
        base = page_url.split("?")[0].rstrip("/")
        if "/@" in base or "/channel/" in base or "/c/" in base or "/user/" in base:
            if "/about" not in base:
                to_fetch.append(base + "/about")

    hubs_fetched = 0
    seen_fetch: set[str] = set()

    for u in to_fetch:
        u = u.strip()
        if not u or u in seen_fetch:
            continue
        seen_fetch.add(u)
        html = fetch_html(u)
        urls = extract_urls_from_html(html)

        for raw in urls:
            raw = raw.strip().rstrip(".,);")
            plat = _platform_for_discovered_url(raw)
            if plat and plat != "__link_hub__" and plat not in out:
                out[plat] = normalize_profile_url(raw, plat)
            elif plat == "__link_hub__" and hubs_fetched < 3:
                hubs_fetched += 1
                inner = fetch_html(raw)
                for raw2 in extract_urls_from_html(inner):
                    raw2 = raw2.strip().rstrip(".,);")
                    p2 = _platform_for_discovered_url(raw2)
                    if p2 and p2 != "__link_hub__" and p2 not in out:
                        out[p2] = normalize_profile_url(raw2, p2)

    return out


def enrich_row_from_anchor_profiles(df: pd.DataFrame, row_label: object) -> None:
    """If one platform is high-confidence, mine that page for other official links."""
    anchor_order = ["Instagram", "YouTube", "X", "Facebook", "TikTok"]
    confs = ROW_PLATFORM_CONFIDENCE.get(row_label, {})

    best_plat: Optional[str] = None
    best_url: str = ""
    best_c: float = 0.0

    for p in anchor_order:
        url = str(df.at[row_label, p] or "").strip()
        if not url:
            continue
        c = float(confs.get(p, 0.0))
        if c < ANCHOR_MIN_CONFIDENCE:
            continue
        if c > best_c:
            best_plat, best_url, best_c = p, url, c

    if not best_url or not best_plat:
        return

    talent = str(df.at[row_label, "Talent Name"] or "")
    print(f"[ENRICH] {talent} ← anchor {best_plat} (conf={best_c:.2f})")

    try:
        discovered = extract_social_links_from_page(best_url, best_plat)
    except Exception as exc:
        print(f"[WARN] enrich failed: {exc}")
        return

    for tgt, link in discovered.items():
        if tgt not in PLATFORMS:
            continue
        cur = str(df.at[row_label, tgt] or "").strip()
        if cur:
            continue
        if not is_valid_profile_url(link, tgt):
            continue
        df.at[row_label, tgt] = link
        conf_value = round(min(0.93, best_c * 0.96), 3)
        ROW_PLATFORM_CONFIDENCE.setdefault(row_label, {})[tgt] = conf_value
        df.at[row_label, PLATFORM_CONF_COLUMNS[tgt]] = conf_value
        ROW_PLATFORM_SOURCE.setdefault(row_label, {})[tgt] = "bio_enrich"
        print(f"  + filled {tgt} from bio/link hub")

    _refresh_row_aggregate_confidence(df, row_label)


def _refresh_row_aggregate_confidence(df: pd.DataFrame, row_label: object) -> None:
    parts: List[float] = []
    for p in PLATFORMS:
        if not str(df.at[row_label, p] or "").strip():
            continue
        parts.append(float(ROW_PLATFORM_CONFIDENCE.get(row_label, {}).get(p, 0.0)))
    if parts:
        df.at[row_label, "Confidence"] = round(sum(parts) / len(parts), 4)


def _refresh_row_source_cell(df: pd.DataFrame, row_label: object) -> None:
    """Compact provenance for Excel: Platform:search | Platform:bio_enrich | …"""
    parts: List[str] = []
    for p in PLATFORMS:
        url = str(df.at[row_label, p] or "").strip()
        if not url:
            continue
        src = ROW_PLATFORM_SOURCE.get(row_label, {}).get(p, "")
        if src:
            parts.append(f"{p}:{src}")
    df.at[row_label, "Source"] = "; ".join(parts)


def serper_search(query: str, num_results: int = 10) -> List[dict]:
    url = "https://google.serper.dev/search"
    headers = {
        "X-API-KEY": SERPER_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "q": query,
        "num": max(1, min(num_results, 10)),
    }

    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()

    structured_results = []
    for item in data.get("organic", []):
        structured_results.append(
            {
                "title": item.get("title", "") or "",
                "snippet": item.get("snippet", "") or "",
                "link": item.get("link", "") or "",
            }
        )

    return structured_results


def _extract_json_obj(text: str) -> dict:
    if not text:
        raise ValueError("Empty OpenAI response.")
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in OpenAI response.")
    return json.loads(text[start : end + 1])


def ai_select_best_profile(
    talent: str,
    platform: str,
    candidates: List[dict],
    entity_category: str,
    entity_sub_category: str,
    search_keywords: str,
) -> dict:
    if not candidates:
        return {"best_link": "Not Found", "confidence": 0.0, "reason": "No candidates provided."}

    system_msg = (
        "You are an expert social profile resolver. "
        "You must choose exactly ONE URL from the candidates list. "
        "NEVER choose post, video, reel, shorts, status, or search URLs. "
        "ONLY profile or channel URLs for the given platform."
    )

    user_payload = {
        "task": "Pick the single best official profile/channel URL for this talent on this platform.",
        "talent": talent,
        "platform": platform,
        "entity_metadata": {
            "title_category": entity_category or "",
            "title_sub_category": entity_sub_category or "",
            "search_keywords": search_keywords or "",
        },
        "candidates": candidates,
        "hard_rules": [
            "Select ONLY from candidates[].link values (or use empty string if none fit).",
            "The chosen URL must be a profile page or channel page, not content.",
            "Reject any URL that looks like a post, video, reel, story, shorts, or status page.",
            "Prefer verified/official signals in title or snippet.",
            "Prefer exact talent name match when evident.",
            "Use entity_metadata (especially title_sub_category: Gender, Talent Type, Athlete, Basketball, etc.) to DISAMBIGUATE namesakes.",
            "If entity_metadata says Gender-Man and Talent Subtype includes Athlete/Basketball, REJECT profiles that are clearly a different person: real estate agents, Realtors, mortgage/digital creators, or unrelated women when the talent should be a male athlete.",
            "If the snippet/title suggests 'realtor', 'real estate', 'Florida Homes', 'digital creator' without any basketball/sports context, treat as WRONG PERSON and return best_link empty.",
            "When uncertain between two similar names, return empty string rather than guessing.",
            "If NO candidate clearly belongs to this talent, return best_link as empty string and confidence under 0.35.",
        ],
        "output_format": {
            "best_link": "exactly one of candidate links OR empty string if uncertain",
            "confidence": "float 0 to 1",
            "reason": "one short sentence",
        },
        "return_only": "strict JSON object, no markdown, no extra keys",
    }

    body = {
        "model": OPENAI_CHAT_MODEL,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
        ],
    }
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers=headers,
        json=body,
        timeout=45,
    )
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]

    parsed = _extract_json_obj(content)
    best_link = parsed.get("best_link", "")
    confidence = parsed.get("confidence", 0.0)
    reason = parsed.get("reason", "")

    if not isinstance(best_link, str):
        best_link = ""
    best_link = best_link.strip()
    try:
        confidence = float(confidence)
    except Exception as exc:
        raise ValueError("OpenAI returned non-numeric confidence.") from exc
    confidence = max(0.0, min(1.0, confidence))
    if not isinstance(reason, str):
        reason = str(reason)

    if not best_link:
        return {"best_link": "", "confidence": confidence, "reason": reason.strip() or "No confident match."}

    return {"best_link": best_link, "confidence": confidence, "reason": reason.strip()}


def search_one_platform(
    talent: str,
    platform: str,
    domains: List[str],
    title_category: str,
    title_sub_category: str,
) -> Tuple[str, str, float, str]:
    search_keywords = extract_search_keywords(title_category, title_sub_category)
    all_candidates: List[dict] = []
    seen_links = set()
    queries = build_queries(talent, platform, domains, search_keywords, title_category, title_sub_category)

    for query in queries:
        try:
            results = serper_search(query, num_results=RESULTS_PER_QUERY)
            print(f"[QUERY] {platform} | {talent} | '{query}' -> {len(results)} raw results")
            for item in results:
                link = item.get("link", "")
                if link and link not in seen_links:
                    seen_links.add(link)
                    all_candidates.append(item)
        except Exception as exc:
            print(f"[WARN] Serper failed for query '{query}': {exc}")

        if len(all_candidates) >= RESULTS_PER_QUERY:
            break
        time.sleep(0.2)

    valid_candidates = [c for c in all_candidates if is_valid_profile_url(c.get("link", ""), platform)]
    valid_candidates = sort_candidates_for_ai(
        talent, valid_candidates, search_keywords, title_category, title_sub_category
    )
    top_candidates = valid_candidates[:MAX_CANDIDATES_FOR_AI]
    ctx = f" | kw: {search_keywords}" if search_keywords else ""
    print(f"[INFO] {platform} | {talent}{ctx} -> {len(top_candidates)} profile-filtered candidates for AI")

    if not top_candidates:
        return platform, "", 0.0, "No valid profile/channel URLs in search results."

    top_candidate = top_candidates[0]
    fallback = first_valid_profile_link(top_candidates, platform)
    try:
        ai_result = ai_select_best_profile(
            talent,
            platform,
            top_candidates,
            title_category,
            title_sub_category,
            search_keywords,
        )
        selected = ai_result["best_link"]
        confidence = ai_result["confidence"]
        reason = ai_result["reason"]

        if not selected:
            if fallback:
                rej_fb, _ = entity_profile_rejected(
                    talent, title_category, title_sub_category, top_candidate
                )
                if not rej_fb:
                    selected = fallback
                    confidence = min(confidence, 0.42)
                    reason = (reason or "") + " | AI empty; trying top-ranked candidate."
        elif not is_valid_profile_url(selected, platform):
            print(f"[WARN] AI picked non-profile URL; trying fallback.")
            if fallback:
                rej_fb, _ = entity_profile_rejected(
                    talent, title_category, title_sub_category, top_candidate
                )
                if not rej_fb:
                    selected = fallback
                    confidence = min(confidence, 0.42)
                    reason = f"{reason} (invalid AI URL)"

        emit_candidate: Optional[dict] = None
        if selected:
            sel_norm = normalize_profile_url(selected, platform).rstrip("/")
            cand = next(
                (
                    c
                    for c in top_candidates
                    if normalize_profile_url(c.get("link", ""), platform).rstrip("/") == sel_norm
                ),
                None,
            )
            emit_candidate = cand
            if cand:
                rej, why = entity_profile_rejected(talent, title_category, title_sub_category, cand)
                if rej:
                    print(f"[REJECT] {platform} | {talent} | {why}")
                    selected = ""
                    confidence = min(confidence, 0.15)
                    reason = why
                    emit_candidate = None

        emit, conf_out, rsn_out = decide_emitted_link(
            talent,
            platform,
            selected or "",
            confidence,
            reason,
            top_candidate,
            search_keywords,
            title_category,
            title_sub_category,
            emit_candidate,
        )
        disp = emit or "(blank)"
        print(f"[SELECTED] {platform} -> {disp} | confidence={conf_out:.2f} | {rsn_out}")
        time.sleep(OPENAI_DELAY_SECONDS)
        return platform, emit, conf_out, rsn_out
    except Exception as exc:
        print(f"[WARN] OpenAI failed for {talent}/{platform}: {exc}")
        if fallback:
            emit, conf_out, rsn_out = decide_emitted_link(
                talent,
                platform,
                fallback,
                0.35,
                f"OpenAI error: {exc}",
                top_candidate,
                search_keywords,
                title_category,
                title_sub_category,
                top_candidate,
            )
        else:
            emit, conf_out, rsn_out = "", 0.0, f"OpenAI error: {exc}"
        time.sleep(OPENAI_DELAY_SECONDS)
        return platform, emit, conf_out, rsn_out


def process_row(idx: object, row: pd.Series, df: pd.DataFrame) -> None:
    talent = str(row.get("Talent Name", "") or "").strip()
    if not talent:
        return

    cat = str(row.get("title_category", "") or "").strip()
    sub = str(row.get("title_sub_category", "") or "").strip()

    confidences: List[float] = []
    ROW_PLATFORM_CONFIDENCE[idx] = {}
    ROW_PLATFORM_SOURCE[idx] = {}
    for p in PLATFORMS:
        if str(row.get(p, "") or "").strip():
            ROW_PLATFORM_SOURCE[idx][p] = "input"
            ROW_PLATFORM_CONFIDENCE[idx][p] = 1.0
            df.at[idx, PLATFORM_CONF_COLUMNS[p]] = 1.0

    futures = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for platform, domains in PLATFORMS.items():
            current_value = str(row.get(platform, "") or "").strip()
            if current_value:
                continue
            fut = executor.submit(search_one_platform, talent, platform, domains, cat, sub)
            futures[fut] = platform

        for fut in as_completed(futures):
            platform, best_link, conf, rsn = fut.result()
            df.at[idx, platform] = best_link
            ROW_PLATFORM_CONFIDENCE[idx][platform] = float(conf) if best_link else 0.0
            df.at[idx, PLATFORM_CONF_COLUMNS[platform]] = float(conf) if best_link else 0.0
            if best_link:
                ROW_PLATFORM_SOURCE[idx][platform] = "search"
            confidences.append(float(conf))
            # Reasons stay in console only ([SELECTED] logs); not written to Excel.

    if confidences:
        df.at[idx, "Confidence"] = sum(confidences) / len(confidences)
    else:
        df.at[idx, "Confidence"] = 0.0

    enrich_row_from_anchor_profiles(df, idx)
    _refresh_row_source_cell(df, idx)


def run_pipeline_on_dataframe(
    df: pd.DataFrame,
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> pd.DataFrame:
    """
    Run the full lookup pipeline on a prepared dataframe.
    Optional progress(1-based index, total rows, talent name) is invoked before each row.
    """
    ROW_PLATFORM_CONFIDENCE.clear()
    ROW_PLATFORM_SOURCE.clear()
    print(f"Initialized talent dataframe with {len(df)} rows.")
    total = len(df)

    for i, (idx, row) in enumerate(df.iterrows(), start=1):
        talent_name = str(row.get("Talent Name", "") or "")
        if progress:
            progress(i, total, talent_name)
        kw = extract_search_keywords(
            str(row.get("title_category", "") or ""),
            str(row.get("title_sub_category", "") or ""),
        )
        extra = f" | metadata: {kw}" if kw else ""
        print(f"\nProcessing talent {i}/{total}: {row['Talent Name']}{extra}")
        process_row(idx, row, df)
        time.sleep(random.uniform(*REQUEST_DELAY_BETWEEN_TALENTS))

    return df


def run_pipeline_for_names(
    names: List[str],
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> pd.DataFrame:
    """Build a dataframe from a plain name list and run the pipeline (e.g. API / UI)."""
    clean = [n.strip() for n in names if n and str(n).strip()]
    if not clean:
        raise ValueError("At least one non-empty name is required.")
    df = build_talent_df(clean, list(PLATFORMS.keys()))
    return run_pipeline_on_dataframe(df, progress=progress)


def run_pipeline() -> pd.DataFrame:
    return run_pipeline_on_dataframe(load_talent_table())


def apply_excel_formatting(path: str, df: pd.DataFrame) -> None:
    try:
        from openpyxl import load_workbook
        from openpyxl.styles import PatternFill, Font
    except ImportError:
        print("[WARN] openpyxl not installed; skipping Excel formatting. pip install openpyxl")
        return

    wb = load_workbook(path)
    ws = wb.active
    headers = [str(c.value) if c.value is not None else "" for c in ws[1]]
    col_index = {h: i + 1 for i, h in enumerate(headers)}

    green = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    yellow = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
    red_conf = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    red_row = PatternFill(start_color="FF9999", end_color="FF9999", fill_type="solid")

    conf_col = col_index.get("Confidence")
    platform_cols = [col_index[p] for p in PLATFORMS.keys() if p in col_index]

    for r in range(2, ws.max_row + 1):
        talent_cell = ws.cell(row=r, column=col_index.get("Talent Name", 1))
        talent_val = str(talent_cell.value or "").strip()
        first_only = is_first_name_only(talent_val)

        if conf_col:
            val = ws.cell(row=r, column=conf_col).value
            try:
                v = float(val)
            except (TypeError, ValueError):
                v = 0.0
            fill = red_conf
            if v > 0.8:
                fill = green
            elif v >= 0.5:
                fill = yellow
            ws.cell(row=r, column=conf_col).fill = fill

        if first_only:
            for c in range(1, ws.max_column + 1):
                cell = ws.cell(row=r, column=c)
                cell.fill = red_row
                if c in platform_cols:
                    cell.font = Font(color="9C0006")

    wb.save(path)


def save_output(df: pd.DataFrame, output_dir: Optional[Path] = None) -> str:
    """Write Excel next to this script unless output_dir is set."""
    base = output_dir if output_dir is not None else Path(__file__).resolve().parent
    base.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"Talent_Social_Lookup_{timestamp}.xlsx"
    path = base / filename
    out = df.copy()
    if "Reason" in out.columns:
        out = out.drop(columns=["Reason"])
    out.to_excel(path, index=False)
    path_str = str(path.resolve())
    print(f"\nSaved output: {path_str}")
    apply_excel_formatting(path_str, out)
    return path_str


if __name__ == "__main__":
    final_df = run_pipeline()
    save_output(final_df)
    print("\nFinal DataFrame:")
    print(final_df)
