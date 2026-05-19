# import json
# import os
# import random
# import re
# import time
# from concurrent.futures import ThreadPoolExecutor, as_completed
# from datetime import datetime
# from pathlib import Path
# from typing import Callable, Dict, List, Optional, Tuple
# from urllib.parse import urlparse

# import pandas as pd
# import requests

# try:
#     from dotenv import load_dotenv

#     load_dotenv(Path(__file__).resolve().parent / ".env")
# except ImportError:
#     pass

# # ================== API KEYS (set in .env: SERPER_API_KEY, OPENAI_API_KEY) ==================
# SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "").strip()
# OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()

# OPENAI_CHAT_MODEL = os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")

# # Optional: load names from Excel in same folder as this script
# TEST_BRANDS_PATH = Path(__file__).resolve().parent / "Demo_Social.xlsx"

# # ================== INPUT (used if Demo_Social.xlsx is missing) ==================
# talent_names = [
#     "Britney Vest",
#     "Ari Melber",
#     "Alyssa Anderson",
#     "Andrea",
#     "Anastasia Pagonis",
# ]

# # ================== CONFIG ==================
# RESULTS_PER_QUERY = 10
# MAX_CANDIDATES_FOR_AI = 5
# MAX_WORKERS = 3
# REQUEST_DELAY_BETWEEN_TALENTS = (1.0, 2.0)
# OPENAI_DELAY_SECONDS = 0.4

# # Emit a profile URL only if confidence is at least this (otherwise leave cell blank).
# MIN_CONFIDENCE_EMIT = float(os.environ.get("MIN_CONFIDENCE_EMIT", "0.72"))
# # Use a high-confidence profile page to discover other platforms (bio / Linktree / about).
# ANCHOR_MIN_CONFIDENCE = float(os.environ.get("ANCHOR_MIN_CONFIDENCE", "0.86"))
# # Only use deterministic fallback (first ranked candidate) if rank score is very strong.
# MIN_RANK_SCORE_FOR_FALLBACK = float(os.environ.get("MIN_RANK_SCORE_FOR_FALLBACK", "12.0"))

# FETCH_HEADERS = {
#     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
#     "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
#     "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
#     "Accept-Language": "en-US,en;q=0.9",
# }

# # Per-row per-platform confidence (filled in process_row) for enrichment step.
# ROW_PLATFORM_CONFIDENCE: Dict[object, Dict[str, float]] = {}
# # Per-row per-platform provenance: "input" | "search" | "bio_enrich"
# ROW_PLATFORM_SOURCE: Dict[object, Dict[str, str]] = {}

# PLATFORMS: Dict[str, List[str]] = {
#     "Facebook": ["facebook.com"],
#     "Instagram": ["instagram.com"],
#     "X": ["x.com", "twitter.com"],
#     "TikTok": ["tiktok.com"],
#     "YouTube": ["youtube.com"],
# }

# PLATFORM_CONF_COLUMNS: Dict[str, str] = {
#     p: f"{p} Confidence" for p in PLATFORMS
# }


# def is_first_name_only(talent: str) -> bool:
#     parts = re.sub(r"\s+", " ", (talent or "").strip()).split()
#     return len(parts) == 1 and bool(parts[0])


# def _find_column(raw: pd.DataFrame, *candidates: str) -> Optional[str]:
#     cmap = {str(c).strip().lower(): c for c in raw.columns}
#     for cand in candidates:
#         if cand.lower() in cmap:
#             return cmap[cand.lower()]
#     return None


# def extract_search_keywords(title_category: str, title_sub_category: str) -> str:
#     """
#     Turn category + sub_category into a short phrase for search queries and ranking.
#     Strips noisy labels like 'Talent Type -' so queries stay focused.
#     """
#     parts: List[str] = []
#     for raw in (title_category, title_sub_category):
#         if raw is None or (isinstance(raw, float) and pd.isna(raw)):
#             continue
#         s = str(raw).strip()
#         if not s or s.lower() == "nan":
#             continue
#         parts.append(s)
#     if not parts:
#         return ""
#     text = " ".join(parts)
#     text = text.replace(",", " ").replace("|", " ")
#     text = re.sub(r"[\r\n\t]+", " ", text)
#     # Remove repeated label prefixes (keeps e.g. Basketball, Football, Musician)
#     text = re.sub(
#         r"(?i)\b(talent type|gender|talent subtype|publication type)\s*-\s*",
#         " ",
#         text,
#     )
#     text = re.sub(r"\s+", " ", text).strip()
#     # Cap length so Serper queries stay readable
#     words = text.split()
#     text = " ".join(words[:14])[:140].strip()
#     return text


# def _default_talent_table() -> pd.DataFrame:
#     n = len(talent_names)
#     data: Dict[str, List] = {
#         "Talent Name": list(talent_names),
#         "title_category": [""] * n,
#         "title_sub_category": [""] * n,
#     }
#     for p in PLATFORMS:
#         data[p] = [""] * n
#     for c in PLATFORM_CONF_COLUMNS.values():
#         data[c] = [float("nan")] * n
#     data["Confidence"] = [float("nan")] * n
#     data["Source"] = [""] * n
#     return pd.DataFrame(data)


# def load_talent_table_from_path(excel_path: Path) -> pd.DataFrame:
#     """
#     Load Talent Name + optional title_category / title_sub_category from an .xlsx/.xls file.
#     Raises ValueError if the file is unreadable or contains no valid names.
#     """
#     excel_path = Path(excel_path)
#     if not excel_path.is_file():
#         raise ValueError(f"File not found: {excel_path}")

#     suffix = excel_path.suffix.lower()
#     try:
#         if suffix == ".csv":
#             raw = pd.read_csv(excel_path)
#         else:
#             raw = pd.read_excel(excel_path)
#     except Exception as exc:
#         raise ValueError(f"Could not read spreadsheet: {exc}") from exc

#     if raw.empty:
#         raise ValueError("The file has no rows.")

#     name_col = _find_column(raw, "Talent Name", "Talent", "title", "Title", "Name")
#     if name_col is None:
#         name_col = raw.columns[0]

#     cat_col = _find_column(
#         raw,
#         "title_category",
#         "de_category",
#         "category",
#         "Title Category",
#     )
#     sub_col = _find_column(raw, "title_sub_category", "sub_category", "Title Sub Category", "subtitle")

#     names_list: List[str] = []
#     cat_list: List[str] = []
#     sub_list: List[str] = []

#     for i in range(len(raw)):
#         name = str(raw.iloc[i][name_col]).strip()
#         if not name or name.lower() == "nan":
#             continue
#         names_list.append(name)
#         c = raw.iloc[i][cat_col] if cat_col else ""
#         s = raw.iloc[i][sub_col] if sub_col else ""
#         cat_list.append("" if pd.isna(c) else str(c).strip())
#         sub_list.append("" if pd.isna(s) else str(s).strip())

#     if not names_list:
#         raise ValueError("No valid talent names found (need a Talent Name column or data in the first column).")

#     n = len(names_list)
#     out: Dict[str, List] = {
#         "Talent Name": names_list,
#         "title_category": cat_list,
#         "title_sub_category": sub_list,
#     }
#     for p in PLATFORMS:
#         out[p] = [""] * n
#     for c in PLATFORM_CONF_COLUMNS.values():
#         out[c] = [float("nan")] * n
#     out["Confidence"] = [float("nan")] * n
#     out["Source"] = [""] * n
#     return pd.DataFrame(out)


# def load_talent_table() -> pd.DataFrame:
#     """Load Talent Name + optional title_category / title_sub_category from Excel or defaults."""
#     if not TEST_BRANDS_PATH.exists():
#         return _default_talent_table()

#     try:
#         return load_talent_table_from_path(TEST_BRANDS_PATH)
#     except ValueError as exc:
#         print(f"[WARN] {exc}. Using default talent_names.")
#         return _default_talent_table()


# def build_talent_df(names: List[str], platforms: List[str]) -> pd.DataFrame:
#     """Legacy helper: names only, no metadata columns."""
#     talent_data: Dict[str, List] = {"Talent Name": names}
#     for platform in platforms:
#         talent_data[platform] = [""] * len(names)
#     for platform in platforms:
#         talent_data[f"{platform} Confidence"] = [float("nan")] * len(names)
#     talent_data["title_category"] = [""] * len(names)
#     talent_data["title_sub_category"] = [""] * len(names)
#     talent_data["Confidence"] = [float("nan")] * len(names)
#     talent_data["Source"] = [""] * len(names)
#     return pd.DataFrame(talent_data)


# def build_queries(
#     talent: str,
#     platform: str,
#     domains: List[str],
#     search_keywords: str,
#     title_category: str = "",
#     title_sub_category: str = "",
# ) -> List[str]:
#     """
#     Build Serper queries. When search_keywords is non-empty (from title_category +
#     title_sub_category), add disambiguated queries so results match the right entity.
#     """
#     kw = (search_keywords or "").strip()
#     queries: List[str] = []
#     exp = parse_entity_expectations(title_category, title_sub_category)

#     # Sport-first queries when Excel says basketball / male athlete (reduces realtor namesakes)
#     if exp["expects_male"] and exp["expects_basketball"]:
#         for domain in domains:
#             queries.append(f'site:{domain} "{talent}" basketball')
#             queries.append(f'site:{domain} "{talent}" basketball player')
#             queries.append(f'site:{domain} "{talent}" NCAA basketball')

#     for domain in domains:
#         queries.append(f'site:{domain} "{talent}" official')
#         queries.append(f'site:{domain} "{talent}" verified')
#         queries.append(f'site:{domain} "{talent}"')
#         if kw:
#             queries.append(f'site:{domain} "{talent}" {kw} official')
#             queries.append(f'site:{domain} "{talent}" {kw}')

#     queries.append(f'"{talent}" {platform} official')
#     queries.append(f'"{talent}" {platform}')
#     if kw:
#         queries.append(f'"{talent}" {kw} {platform} official')
#         queries.append(f'"{talent}" {kw} {platform}')

#     # De-dupe while preserving order
#     seen: set[str] = set()
#     unique: List[str] = []
#     for q in queries:
#         if q not in seen:
#             seen.add(q)
#             unique.append(q)
#     return unique


# def is_valid_profile_url(link: str, platform: str) -> bool:
#     """
#     Return True only for profile/channel URLs, not posts, videos, reels, etc.
#     """
#     if not isinstance(link, str) or not link.strip():
#         return False
#     u = link.strip()
#     try:
#         parsed = urlparse(u)
#     except Exception:
#         return False
#     if parsed.scheme not in ("http", "https"):
#         return False
#     host = (parsed.netloc or "").lower()
#     path = (parsed.path or "").lower()
#     full = u.lower()

#     if platform == "Facebook":
#         if "facebook.com" not in host:
#             return False
#         if any(seg in full for seg in ("/posts/", "/photos/", "/videos/", "/watch/", "/reel", "/story.php", "/permalink/")):
#             return False
#         if "profile.php" in path or "/people/" in path or "/pages/" in path:
#             return True
#         segs = [s for s in path.strip("/").split("/") if s]
#         if len(segs) == 1 and segs[0] not in ("share", "sharer", "groups", "events", "marketplace", "gaming", "watch"):
#             return True
#         return False

#     if platform == "Instagram":
#         if "instagram.com" not in host:
#             return False
#         if any(x in full for x in ("/p/", "/reel", "/reels/", "/stories/", "/tv/", "/explore/", "/tags/", "/locations/")):
#             return False
#         segs = [s for s in path.strip("/").split("/") if s]
#         if len(segs) == 1:
#             return True
#         return False

#     if platform == "YouTube":
#         if "youtube.com" not in host and "youtu.be" not in host:
#             return False
#         if any(x in full for x in ("/watch", "/shorts/", "/playlist", "/results", "/live/", "/feed/", "/attribution_link")):
#             return False
#         if "/@" in full or "/channel/" in full or "/c/" in full or "/user/" in full:
#             return True
#         return False

#     if platform == "X":
#         if "x.com" not in host and "twitter.com" not in host:
#             return False
#         if "/status/" in full or "/i/" in full or "/intent/" in full or "/search" in full:
#             return False
#         segs = [s for s in path.strip("/").split("/") if s]
#         if len(segs) == 1:
#             return True
#         return False

#     if platform == "TikTok":
#         if "tiktok.com" not in host:
#             return False
#         if any(x in full for x in ("/video/", "/tag/", "/music/", "/discover", "/foryou")):
#             return False
#         if re.search(r"tiktok\.com/@[^/]+/?$", full):
#             return True
#         return False

#     return False


# def candidate_rank_score(
#     talent: str,
#     c: dict,
#     search_keywords: str,
#     title_category: str = "",
#     title_sub_category: str = "",
# ) -> float:
#     title = (c.get("title") or "").lower()
#     snippet = (c.get("snippet") or "").lower()
#     link = (c.get("link") or "").lower()
#     t = re.sub(r"\s+", " ", (talent or "").strip()).lower()
#     score = 0.0
#     if "official" in title or "official" in snippet:
#         score += 4.0
#     if "verified" in title or "verified" in snippet or "✓" in (c.get("title") or ""):
#         score += 3.0
#     if t and t in title:
#         score += 3.0
#     if t and t in snippet:
#         score += 2.0
#     if t and t in link.replace("-", " "):
#         score += 1.0
#     # Metadata alignment (e.g. basketball, sports publisher)
#     for token in _metadata_tokens(search_keywords):
#         if len(token) < 3:
#             continue
#         if token in title or token in snippet:
#             score += 1.5
#     rej, _ = entity_profile_rejected(talent, title_category, title_sub_category, c)
#     if rej:
#         score -= 35.0
#     return score


# def _metadata_tokens(search_keywords: str) -> List[str]:
#     if not search_keywords:
#         return []
#     parts = re.split(r"[^\w]+", search_keywords.lower())
#     stop = {
#         "the",
#         "and",
#         "for",
#         "type",
#         "talent",
#         "gender",
#         "subtype",
#         "publication",
#         "network",
#     }
#     # Keep man/woman/basketball/athlete for ranking when present in keywords
#     return [p for p in parts if p and p not in stop and len(p) > 2]


# def parse_entity_expectations(title_category: str, title_sub_category: str) -> Dict[str, bool]:
#     """Structured signals from Excel category + subcategory (e.g. Gender - Man, Athlete - Basketball)."""
#     blob = f"{title_category or ''} {title_sub_category or ''}".lower()
#     return {
#         "expects_male": bool(re.search(r"gender\s*-\s*man\b", blob)),
#         "expects_female": bool(re.search(r"gender\s*-\s*woman\b", blob)),
#         "expects_athlete": bool(
#             re.search(r"\bathlete\b", blob)
#             or re.search(r"\bbasketball\b", blob)
#             or re.search(r"\bfootball\b", blob)
#             or "sport" in blob
#         ),
#         "expects_basketball": "basketball" in blob,
#     }


# def entity_profile_rejected(
#     talent: str,
#     title_category: str,
#     title_sub_category: str,
#     candidate: Optional[dict],
# ) -> Tuple[bool, str]:
#     """
#     Reject Serper candidates that clearly contradict Excel metadata (wrong industry/person).
#     Prefer blank cells over wrong Facebook/etc. links for namesakes.
#     """
#     if not candidate:
#         return False, ""
#     title = (candidate.get("title") or "")
#     snippet = (candidate.get("snippet") or "")
#     blob = f"{title} {snippet}".lower()
#     exp = parse_entity_expectations(title_category, title_sub_category)

#     sport_markers = (
#         "basketball",
#         "nba",
#         "wnba",
#         "ncaa",
#         "college basketball",
#         "draft",
#         "athlete",
#         "espn",
#         "sport",
#         "point guard",
#         "shooting guard",
#         "forward",
#         "center",
#         "hoops",
#         "nba draft",
#     )
#     sport_hit = any(m in blob for m in sport_markers)

#     non_sport_professions = (
#         "realtor",
#         "real estate",
#         "mortgage",
#         "homes realty",
#         "florida homes",
#         "digital creator",
#         "realtor sales",
#         "realty & mortgage",
#         "realty and mortgage",
#         "listing agent",
#     )
#     non_sport_hit = any(m in blob for m in non_sport_professions)

#     # Male + athlete (esp. basketball): do not accept obvious realtor / unrelated creator pages
#     if exp["expects_male"] and (exp["expects_athlete"] or exp["expects_basketball"]):
#         if non_sport_hit and not sport_hit:
#             return (
#                 True,
#                 "Metadata indicates a male athlete; this result looks like realtor/creator/real estate, not sports.",
#             )
#         # Common female first names in title/snippet when we expect a man + athlete (namesake)
#         female_name_hits = (
#             "bobbie ",
#             " bobbie",
#             "brittany ",
#             "britney ",
#             "jessica ",
#             "samantha ",
#             "miss ",
#             " mrs ",
#         )
#         if any(x in blob for x in female_name_hits) and not sport_hit:
#             return (
#                 True,
#                 "Profile text suggests a different person (female-leaning name/role) vs Gender-Man athlete metadata.",
#             )

#     if exp["expects_female"] and exp["expects_athlete"] and non_sport_hit and not sport_hit:
#         male_lean = (" mr ", "his ", "his own", "father", "husband")
#         if any(x in blob for x in male_lean) and "woman" not in blob:
#             return True, "Metadata indicates female athlete; result looks unrelated (non-sports professional)."

#     return False, ""


# def sort_candidates_for_ai(
#     talent: str,
#     candidates: List[dict],
#     search_keywords: str,
#     title_category: str = "",
#     title_sub_category: str = "",
# ) -> List[dict]:
#     return sorted(
#         candidates,
#         key=lambda c: -candidate_rank_score(
#             talent, c, search_keywords, title_category, title_sub_category
#         ),
#     )


# def first_valid_profile_link(candidates: List[dict], platform: str) -> str:
#     for item in candidates:
#         link = item.get("link", "")
#         if is_valid_profile_url(link, platform):
#             return normalize_profile_url(link, platform)
#     return ""


# def normalize_profile_url(url: str, platform: str) -> str:
#     """Normalize host (e.g. mobile YouTube) for consistent output."""
#     if not url or not isinstance(url, str):
#         return ""
#     u = url.strip()
#     if platform == "YouTube":
#         u = u.replace("://m.youtube.com", "://www.youtube.com")
#         u = u.replace("://music.youtube.com", "://www.youtube.com")
#         if "youtube.com" in u and "www." not in urlparse(u).netloc and "m." not in urlparse(u).netloc:
#             u = u.replace("://youtube.com", "://www.youtube.com")
#     return u.rstrip("/")


# def _slug_chars(s: str) -> str:
#     return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


# def talent_url_aligned(talent: str, link: str) -> bool:
#     """Heuristic: name tokens appear in profile URL path (reduces wrong person)."""
#     t = _slug_chars(talent)
#     if len(t) < 4:
#         return False
#     path = urlparse(link).path
#     path_compact = _slug_chars(path)
#     if len(t) >= 6 and t[: min(8, len(t))] in path_compact:
#         return True
#     for part in re.sub(r"\s+", " ", (talent or "").strip()).lower().split():
#         if len(part) < 3:
#             continue
#         sp = _slug_chars(part)
#         if len(sp) >= 5 and sp in path_compact:
#             return True
#     return False


# def decide_emitted_link(
#     talent: str,
#     platform: str,
#     selected: str,
#     confidence: float,
#     reason: str,
#     top_candidate: Optional[dict],
#     search_keywords: str,
#     title_category: str = "",
#     title_sub_category: str = "",
#     emit_candidate: Optional[dict] = None,
# ) -> Tuple[str, float, str]:
#     """Prefer blank cells over wrong links when confidence is low."""
#     if not selected or selected == "Not Found":
#         return "", confidence, reason or "No selection."

#     selected = normalize_profile_url(selected, platform)
#     if not is_valid_profile_url(selected, platform):
#         return "", 0.0, "Rejected: not a valid profile/channel URL."

#     if emit_candidate:
#         rej, why = entity_profile_rejected(
#             talent, title_category, title_sub_category, emit_candidate
#         )
#         if rej:
#             return "", min(confidence, 0.12), why

#     if confidence >= MIN_CONFIDENCE_EMIT:
#         return selected, confidence, reason

#     if top_candidate is not None:
#         rej_fb, rej_msg = entity_profile_rejected(
#             talent, title_category, title_sub_category, top_candidate
#         )
#         if rej_fb:
#             return "", confidence, f"Omitted: {rej_msg}"
#         rs = candidate_rank_score(
#             talent, top_candidate, search_keywords, title_category, title_sub_category
#         )
#         link = top_candidate.get("link", "")
#         if (
#             rs >= MIN_RANK_SCORE_FOR_FALLBACK
#             and talent_url_aligned(talent, link)
#             and is_valid_profile_url(link, platform)
#         ):
#             return (
#                 normalize_profile_url(link, platform),
#                 min(confidence, 0.68),
#                 f"Strong search rank + URL match ({rs:.1f}): {reason}",
#             )

#     return "", confidence, f"Omitted (below {MIN_CONFIDENCE_EMIT:.2f}): {reason}"


# URL_IN_TEXT_RE = re.compile(r"https?://[^\s\"\'<>\)\]]+", re.I)


# def fetch_html(url: str) -> str:
#     try:
#         r = requests.get(url, headers=FETCH_HEADERS, timeout=20, allow_redirects=True)
#         r.raise_for_status()
#         if len(r.content) > 2_500_000:
#             return ""
#         return r.text or ""
#     except Exception as exc:
#         print(f"[WARN] fetch failed {url[:90]}… : {exc}")
#         return ""


# def extract_urls_from_html(html: str) -> List[str]:
#     if not html:
#         return []
#     found: set[str] = set()
#     for m in URL_IN_TEXT_RE.finditer(html):
#         u = m.group(0).rstrip(".,);\\]}\"'")
#         if u.startswith("http"):
#             found.add(u.split("&utm_")[0])
#     for m in re.finditer(r'href\s*=\s*["\']([^"\']+)["\']', html, re.I):
#         h = m.group(1).strip()
#         if h.startswith("http"):
#             found.add(h.split("&utm_")[0])
#     return list(found)


# def _platform_for_discovered_url(url: str) -> Optional[str]:
#     u = url.lower()
#     for plat in PLATFORMS:
#         if is_valid_profile_url(url, plat):
#             return plat
#     if "linktr.ee/" in u or "linktree.com/" in u or "lnk.bio" in u or "beacons.ai" in u:
#         return "__link_hub__"
#     return None


# def extract_social_links_from_page(page_url: str, source_platform: str) -> Dict[str, str]:
#     """
#     Pull external profile URLs from a public page (bio, about, or link-in-bio services).
#     Instagram often blocks scraping; YouTube /about and Linktree work more reliably.
#     """
#     out: Dict[str, str] = {}
#     to_fetch: List[str] = [page_url]

#     if source_platform == "YouTube":
#         base = page_url.split("?")[0].rstrip("/")
#         if "/@" in base or "/channel/" in base or "/c/" in base or "/user/" in base:
#             if "/about" not in base:
#                 to_fetch.append(base + "/about")

#     hubs_fetched = 0
#     seen_fetch: set[str] = set()

#     for u in to_fetch:
#         u = u.strip()
#         if not u or u in seen_fetch:
#             continue
#         seen_fetch.add(u)
#         html = fetch_html(u)
#         urls = extract_urls_from_html(html)

#         for raw in urls:
#             raw = raw.strip().rstrip(".,);")
#             plat = _platform_for_discovered_url(raw)
#             if plat and plat != "__link_hub__" and plat not in out:
#                 out[plat] = normalize_profile_url(raw, plat)
#             elif plat == "__link_hub__" and hubs_fetched < 3:
#                 hubs_fetched += 1
#                 inner = fetch_html(raw)
#                 for raw2 in extract_urls_from_html(inner):
#                     raw2 = raw2.strip().rstrip(".,);")
#                     p2 = _platform_for_discovered_url(raw2)
#                     if p2 and p2 != "__link_hub__" and p2 not in out:
#                         out[p2] = normalize_profile_url(raw2, p2)

#     return out


# def enrich_row_from_anchor_profiles(df: pd.DataFrame, row_label: object) -> None:
#     """If one platform is high-confidence, mine that page for other official links."""
#     anchor_order = ["Instagram", "YouTube", "X", "Facebook", "TikTok"]
#     confs = ROW_PLATFORM_CONFIDENCE.get(row_label, {})

#     best_plat: Optional[str] = None
#     best_url: str = ""
#     best_c: float = 0.0

#     for p in anchor_order:
#         url = str(df.at[row_label, p] or "").strip()
#         if not url:
#             continue
#         c = float(confs.get(p, 0.0))
#         if c < ANCHOR_MIN_CONFIDENCE:
#             continue
#         if c > best_c:
#             best_plat, best_url, best_c = p, url, c

#     if not best_url or not best_plat:
#         return

#     talent = str(df.at[row_label, "Talent Name"] or "")
#     print(f"[ENRICH] {talent} ← anchor {best_plat} (conf={best_c:.2f})")

#     try:
#         discovered = extract_social_links_from_page(best_url, best_plat)
#     except Exception as exc:
#         print(f"[WARN] enrich failed: {exc}")
#         return

#     for tgt, link in discovered.items():
#         if tgt not in PLATFORMS:
#             continue
#         cur = str(df.at[row_label, tgt] or "").strip()
#         if cur:
#             continue
#         if not is_valid_profile_url(link, tgt):
#             continue
#         df.at[row_label, tgt] = link
#         conf_value = round(min(0.93, best_c * 0.96), 3)
#         ROW_PLATFORM_CONFIDENCE.setdefault(row_label, {})[tgt] = conf_value
#         df.at[row_label, PLATFORM_CONF_COLUMNS[tgt]] = conf_value
#         ROW_PLATFORM_SOURCE.setdefault(row_label, {})[tgt] = "bio_enrich"
#         print(f"  + filled {tgt} from bio/link hub")

#     _refresh_row_aggregate_confidence(df, row_label)


# def _refresh_row_aggregate_confidence(df: pd.DataFrame, row_label: object) -> None:
#     parts: List[float] = []
#     for p in PLATFORMS:
#         if not str(df.at[row_label, p] or "").strip():
#             continue
#         parts.append(float(ROW_PLATFORM_CONFIDENCE.get(row_label, {}).get(p, 0.0)))
#     if parts:
#         df.at[row_label, "Confidence"] = round(sum(parts) / len(parts), 4)


# def _refresh_row_source_cell(df: pd.DataFrame, row_label: object) -> None:
#     """Compact provenance for Excel: Platform:search | Platform:bio_enrich | …"""
#     parts: List[str] = []
#     for p in PLATFORMS:
#         url = str(df.at[row_label, p] or "").strip()
#         if not url:
#             continue
#         src = ROW_PLATFORM_SOURCE.get(row_label, {}).get(p, "")
#         if src:
#             parts.append(f"{p}:{src}")
#     df.at[row_label, "Source"] = "; ".join(parts)


# def serper_search(query: str, num_results: int = 10) -> List[dict]:
#     url = "https://google.serper.dev/search"
#     headers = {
#         "X-API-KEY": SERPER_API_KEY,
#         "Content-Type": "application/json",
#     }
#     payload = {
#         "q": query,
#         "num": max(1, min(num_results, 10)),
#     }

#     response = requests.post(url, headers=headers, json=payload, timeout=30)
#     response.raise_for_status()
#     data = response.json()

#     structured_results = []
#     for item in data.get("organic", []):
#         structured_results.append(
#             {
#                 "title": item.get("title", "") or "",
#                 "snippet": item.get("snippet", "") or "",
#                 "link": item.get("link", "") or "",
#             }
#         )

#     return structured_results


# def _extract_json_obj(text: str) -> dict:
#     if not text:
#         raise ValueError("Empty OpenAI response.")
#     start = text.find("{")
#     end = text.rfind("}")
#     if start == -1 or end == -1 or end <= start:
#         raise ValueError("No JSON object found in OpenAI response.")
#     return json.loads(text[start : end + 1])


# def ai_select_best_profile(
#     talent: str,
#     platform: str,
#     candidates: List[dict],
#     entity_category: str,
#     entity_sub_category: str,
#     search_keywords: str,
# ) -> dict:
#     if not candidates:
#         return {"best_link": "Not Found", "confidence": 0.0, "reason": "No candidates provided."}

#     system_msg = (
#         "You are an expert social profile resolver. "
#         "You must choose exactly ONE URL from the candidates list. "
#         "NEVER choose post, video, reel, shorts, status, or search URLs. "
#         "ONLY profile or channel URLs for the given platform."
#     )

#     user_payload = {
#         "task": "Pick the single best official profile/channel URL for this talent on this platform.",
#         "talent": talent,
#         "platform": platform,
#         "entity_metadata": {
#             "title_category": entity_category or "",
#             "title_sub_category": entity_sub_category or "",
#             "search_keywords": search_keywords or "",
#         },
#         "candidates": candidates,
#         "hard_rules": [
#             "Select ONLY from candidates[].link values (or use empty string if none fit).",
#             "The chosen URL must be a profile page or channel page, not content.",
#             "Reject any URL that looks like a post, video, reel, story, shorts, or status page.",
#             "Prefer verified/official signals in title or snippet.",
#             "Prefer exact talent name match when evident.",
#             "Use entity_metadata (especially title_sub_category: Gender, Talent Type, Athlete, Basketball, etc.) to DISAMBIGUATE namesakes.",
#             "If entity_metadata says Gender-Man and Talent Subtype includes Athlete/Basketball, REJECT profiles that are clearly a different person: real estate agents, Realtors, mortgage/digital creators, or unrelated women when the talent should be a male athlete.",
#             "If the snippet/title suggests 'realtor', 'real estate', 'Florida Homes', 'digital creator' without any basketball/sports context, treat as WRONG PERSON and return best_link empty.",
#             "When uncertain between two similar names, return empty string rather than guessing.",
#             "If NO candidate clearly belongs to this talent, return best_link as empty string and confidence under 0.35.",
#         ],
#         "output_format": {
#             "best_link": "exactly one of candidate links OR empty string if uncertain",
#             "confidence": "float 0 to 1",
#             "reason": "one short sentence",
#         },
#         "return_only": "strict JSON object, no markdown, no extra keys",
#     }

#     body = {
#         "model": OPENAI_CHAT_MODEL,
#         "temperature": 0,
#         "messages": [
#             {"role": "system", "content": system_msg},
#             {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
#         ],
#     }
#     headers = {
#         "Authorization": f"Bearer {OPENAI_API_KEY}",
#         "Content-Type": "application/json",
#     }

#     response = requests.post(
#         "https://api.openai.com/v1/chat/completions",
#         headers=headers,
#         json=body,
#         timeout=45,
#     )
#     response.raise_for_status()
#     data = response.json()
#     content = data["choices"][0]["message"]["content"]

#     parsed = _extract_json_obj(content)
#     best_link = parsed.get("best_link", "")
#     confidence = parsed.get("confidence", 0.0)
#     reason = parsed.get("reason", "")

#     if not isinstance(best_link, str):
#         best_link = ""
#     best_link = best_link.strip()
#     try:
#         confidence = float(confidence)
#     except Exception as exc:
#         raise ValueError("OpenAI returned non-numeric confidence.") from exc
#     confidence = max(0.0, min(1.0, confidence))
#     if not isinstance(reason, str):
#         reason = str(reason)

#     if not best_link:
#         return {"best_link": "", "confidence": confidence, "reason": reason.strip() or "No confident match."}

#     return {"best_link": best_link, "confidence": confidence, "reason": reason.strip()}


# def search_one_platform(
#     talent: str,
#     platform: str,
#     domains: List[str],
#     title_category: str,
#     title_sub_category: str,
# ) -> Tuple[str, str, float, str]:
#     search_keywords = extract_search_keywords(title_category, title_sub_category)
#     all_candidates: List[dict] = []
#     seen_links = set()
#     queries = build_queries(talent, platform, domains, search_keywords, title_category, title_sub_category)

#     for query in queries:
#         try:
#             results = serper_search(query, num_results=RESULTS_PER_QUERY)
#             print(f"[QUERY] {platform} | {talent} | '{query}' -> {len(results)} raw results")
#             for item in results:
#                 link = item.get("link", "")
#                 if link and link not in seen_links:
#                     seen_links.add(link)
#                     all_candidates.append(item)
#         except Exception as exc:
#             print(f"[WARN] Serper failed for query '{query}': {exc}")

#         if len(all_candidates) >= RESULTS_PER_QUERY:
#             break
#         time.sleep(0.2)

#     valid_candidates = [c for c in all_candidates if is_valid_profile_url(c.get("link", ""), platform)]
#     valid_candidates = sort_candidates_for_ai(
#         talent, valid_candidates, search_keywords, title_category, title_sub_category
#     )
#     top_candidates = valid_candidates[:MAX_CANDIDATES_FOR_AI]
#     ctx = f" | kw: {search_keywords}" if search_keywords else ""
#     print(f"[INFO] {platform} | {talent}{ctx} -> {len(top_candidates)} profile-filtered candidates for AI")

#     if not top_candidates:
#         return platform, "", 0.0, "No valid profile/channel URLs in search results."

#     top_candidate = top_candidates[0]
#     fallback = first_valid_profile_link(top_candidates, platform)
#     try:
#         ai_result = ai_select_best_profile(
#             talent,
#             platform,
#             top_candidates,
#             title_category,
#             title_sub_category,
#             search_keywords,
#         )
#         selected = ai_result["best_link"]
#         confidence = ai_result["confidence"]
#         reason = ai_result["reason"]

#         if not selected:
#             if fallback:
#                 rej_fb, _ = entity_profile_rejected(
#                     talent, title_category, title_sub_category, top_candidate
#                 )
#                 if not rej_fb:
#                     selected = fallback
#                     confidence = min(confidence, 0.42)
#                     reason = (reason or "") + " | AI empty; trying top-ranked candidate."
#         elif not is_valid_profile_url(selected, platform):
#             print(f"[WARN] AI picked non-profile URL; trying fallback.")
#             if fallback:
#                 rej_fb, _ = entity_profile_rejected(
#                     talent, title_category, title_sub_category, top_candidate
#                 )
#                 if not rej_fb:
#                     selected = fallback
#                     confidence = min(confidence, 0.42)
#                     reason = f"{reason} (invalid AI URL)"

#         emit_candidate: Optional[dict] = None
#         if selected:
#             sel_norm = normalize_profile_url(selected, platform).rstrip("/")
#             cand = next(
#                 (
#                     c
#                     for c in top_candidates
#                     if normalize_profile_url(c.get("link", ""), platform).rstrip("/") == sel_norm
#                 ),
#                 None,
#             )
#             emit_candidate = cand
#             if cand:
#                 rej, why = entity_profile_rejected(talent, title_category, title_sub_category, cand)
#                 if rej:
#                     print(f"[REJECT] {platform} | {talent} | {why}")
#                     selected = ""
#                     confidence = min(confidence, 0.15)
#                     reason = why
#                     emit_candidate = None

#         emit, conf_out, rsn_out = decide_emitted_link(
#             talent,
#             platform,
#             selected or "",
#             confidence,
#             reason,
#             top_candidate,
#             search_keywords,
#             title_category,
#             title_sub_category,
#             emit_candidate,
#         )
#         disp = emit or "(blank)"
#         print(f"[SELECTED] {platform} -> {disp} | confidence={conf_out:.2f} | {rsn_out}")
#         time.sleep(OPENAI_DELAY_SECONDS)
#         return platform, emit, conf_out, rsn_out
#     except Exception as exc:
#         print(f"[WARN] OpenAI failed for {talent}/{platform}: {exc}")
#         if fallback:
#             emit, conf_out, rsn_out = decide_emitted_link(
#                 talent,
#                 platform,
#                 fallback,
#                 0.35,
#                 f"OpenAI error: {exc}",
#                 top_candidate,
#                 search_keywords,
#                 title_category,
#                 title_sub_category,
#                 top_candidate,
#             )
#         else:
#             emit, conf_out, rsn_out = "", 0.0, f"OpenAI error: {exc}"
#         time.sleep(OPENAI_DELAY_SECONDS)
#         return platform, emit, conf_out, rsn_out


# def process_row(idx: object, row: pd.Series, df: pd.DataFrame) -> None:
#     talent = str(row.get("Talent Name", "") or "").strip()
#     if not talent:
#         return

#     cat = str(row.get("title_category", "") or "").strip()
#     sub = str(row.get("title_sub_category", "") or "").strip()

#     confidences: List[float] = []
#     ROW_PLATFORM_CONFIDENCE[idx] = {}
#     ROW_PLATFORM_SOURCE[idx] = {}
#     for p in PLATFORMS:
#         if str(row.get(p, "") or "").strip():
#             ROW_PLATFORM_SOURCE[idx][p] = "input"
#             ROW_PLATFORM_CONFIDENCE[idx][p] = 1.0
#             df.at[idx, PLATFORM_CONF_COLUMNS[p]] = 1.0

#     futures = {}
#     with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
#         for platform, domains in PLATFORMS.items():
#             current_value = str(row.get(platform, "") or "").strip()
#             if current_value:
#                 continue
#             fut = executor.submit(search_one_platform, talent, platform, domains, cat, sub)
#             futures[fut] = platform

#         for fut in as_completed(futures):
#             platform, best_link, conf, rsn = fut.result()
#             df.at[idx, platform] = best_link
#             ROW_PLATFORM_CONFIDENCE[idx][platform] = float(conf) if best_link else 0.0
#             df.at[idx, PLATFORM_CONF_COLUMNS[platform]] = float(conf) if best_link else 0.0
#             if best_link:
#                 ROW_PLATFORM_SOURCE[idx][platform] = "search"
#             confidences.append(float(conf))
#             # Reasons stay in console only ([SELECTED] logs); not written to Excel.

#     if confidences:
#         df.at[idx, "Confidence"] = sum(confidences) / len(confidences)
#     else:
#         df.at[idx, "Confidence"] = 0.0

#     enrich_row_from_anchor_profiles(df, idx)
#     _refresh_row_source_cell(df, idx)


# def run_pipeline_on_dataframe(
#     df: pd.DataFrame,
#     progress: Optional[Callable[[int, int, str], None]] = None,
# ) -> pd.DataFrame:
#     """
#     Run the full lookup pipeline on a prepared dataframe.
#     Optional progress(1-based index, total rows, talent name) is invoked before each row.
#     """
#     ROW_PLATFORM_CONFIDENCE.clear()
#     ROW_PLATFORM_SOURCE.clear()
#     print(f"Initialized talent dataframe with {len(df)} rows.")
#     total = len(df)

#     for i, (idx, row) in enumerate(df.iterrows(), start=1):
#         talent_name = str(row.get("Talent Name", "") or "")
#         if progress:
#             progress(i, total, talent_name)
#         kw = extract_search_keywords(
#             str(row.get("title_category", "") or ""),
#             str(row.get("title_sub_category", "") or ""),
#         )
#         extra = f" | metadata: {kw}" if kw else ""
#         print(f"\nProcessing talent {i}/{total}: {row['Talent Name']}{extra}")
#         process_row(idx, row, df)
#         time.sleep(random.uniform(*REQUEST_DELAY_BETWEEN_TALENTS))

#     return df


# def run_pipeline_for_names(
#     names: List[str],
#     progress: Optional[Callable[[int, int, str], None]] = None,
# ) -> pd.DataFrame:
#     """Build a dataframe from a plain name list and run the pipeline (e.g. API / UI)."""
#     clean = [n.strip() for n in names if n and str(n).strip()]
#     if not clean:
#         raise ValueError("At least one non-empty name is required.")
#     df = build_talent_df(clean, list(PLATFORMS.keys()))
#     return run_pipeline_on_dataframe(df, progress=progress)


# def run_pipeline() -> pd.DataFrame:
#     return run_pipeline_on_dataframe(load_talent_table())


# def apply_excel_formatting(path: str, df: pd.DataFrame) -> None:
#     try:
#         from openpyxl import load_workbook
#         from openpyxl.styles import PatternFill, Font
#     except ImportError:
#         print("[WARN] openpyxl not installed; skipping Excel formatting. pip install openpyxl")
#         return

#     wb = load_workbook(path)
#     ws = wb.active
#     headers = [str(c.value) if c.value is not None else "" for c in ws[1]]
#     col_index = {h: i + 1 for i, h in enumerate(headers)}

#     green = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
#     yellow = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
#     red_conf = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
#     red_row = PatternFill(start_color="FF9999", end_color="FF9999", fill_type="solid")

#     conf_col = col_index.get("Confidence")
#     platform_cols = [col_index[p] for p in PLATFORMS.keys() if p in col_index]

#     for r in range(2, ws.max_row + 1):
#         talent_cell = ws.cell(row=r, column=col_index.get("Talent Name", 1))
#         talent_val = str(talent_cell.value or "").strip()
#         first_only = is_first_name_only(talent_val)

#         if conf_col:
#             val = ws.cell(row=r, column=conf_col).value
#             try:
#                 v = float(val)
#             except (TypeError, ValueError):
#                 v = 0.0
#             fill = red_conf
#             if v > 0.8:
#                 fill = green
#             elif v >= 0.5:
#                 fill = yellow
#             ws.cell(row=r, column=conf_col).fill = fill

#         if first_only:
#             for c in range(1, ws.max_column + 1):
#                 cell = ws.cell(row=r, column=c)
#                 cell.fill = red_row
#                 if c in platform_cols:
#                     cell.font = Font(color="9C0006")

#     wb.save(path)


# def save_output(df: pd.DataFrame, output_dir: Optional[Path] = None) -> str:
#     """Write Excel next to this script unless output_dir is set."""
#     base = output_dir if output_dir is not None else Path(__file__).resolve().parent
#     base.mkdir(parents=True, exist_ok=True)
#     timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
#     filename = f"Talent_Social_Lookup_{timestamp}.xlsx"
#     path = base / filename
#     out = df.copy()
#     if "Reason" in out.columns:
#         out = out.drop(columns=["Reason"])
#     out.to_excel(path, index=False)
#     path_str = str(path.resolve())
#     print(f"\nSaved output: {path_str}")
#     apply_excel_formatting(path_str, out)
#     return path_str


# if __name__ == "__main__":
#     final_df = run_pipeline()
#     save_output(final_df)
#     print("\nFinal DataFrame:")
#     print(final_df)









# testing.py  — Enhanced AI Layer (drop-in replacement)
# =========================================================
# Key improvements over original:
#
#  1. build_candidate_signals()        — pre-AI evidence extraction per candidate
#  2. get_category_disambiguation_context() — category-specific AI rules
#  3. ai_select_best_profile()         — REWRITTEN: two-phase chain-of-thought prompt,
#                                        evidence-aware, blank-preferred, namesake-safe
#  4. ai_verify_selected_link()        — NEW: a quick second AI call that confirms or
#                                        vetoes the chosen link before we emit it
#  5. get_name_ambiguity_level()       — NEW: tightens confidence thresholds for
#                                        single / very-common names
#  6. extract_username_hints()         — NEW: pulls @handle from resolved platforms
#                                        to bias searches + validation on other platforms
#  7. entity_profile_rejected()        — EXPANDED: covers media, music, executive,
#                                        politician, author categories too
#  8. candidate_rank_score()           — EXPANDED: URL-slug alignment, follower signals,
#                                        username hint bonus, generic-handle penalty
#  9. build_queries()                  — EXPANDED: username-hint queries when known
# 10. decide_emitted_link()            — STRICTER: dynamic MIN_CONFIDENCE per name type
# =========================================================

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

# ================== API KEYS ==================
SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_CHAT_MODEL = os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")

TEST_BRANDS_PATH = Path(__file__).resolve().parent / "Demo_Social.xlsx"

# ================== INPUT FALLBACK ==================
talent_names = [
    "Britney Vest",
    "Ari Melber",
    "Alyssa Anderson",
    "Andrea",
    "Anastasia Pagonis",
]

# ================== CONFIG ==================
RESULTS_PER_QUERY         = 10
MAX_CANDIDATES_FOR_AI     = 6   # slightly more for two-phase AI
MAX_WORKERS               = 3
REQUEST_DELAY_BETWEEN_TALENTS = (1.0, 2.0)
OPENAI_DELAY_SECONDS      = 0.4

# Base emit gate — may be raised dynamically for ambiguous names
MIN_CONFIDENCE_EMIT       = float(os.environ.get("MIN_CONFIDENCE_EMIT", "0.72"))
ANCHOR_MIN_CONFIDENCE     = float(os.environ.get("ANCHOR_MIN_CONFIDENCE", "0.86"))
MIN_RANK_SCORE_FOR_FALLBACK = float(os.environ.get("MIN_RANK_SCORE_FOR_FALLBACK", "12.0"))

# Extra gate for AI verify pass — if verify confidence drops below this, veto
AI_VERIFY_MIN_CONFIDENCE  = float(os.environ.get("AI_VERIFY_MIN_CONFIDENCE", "0.62"))

FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Per-row per-platform confidence (filled in process_row)
ROW_PLATFORM_CONFIDENCE: Dict[object, Dict[str, float]] = {}
# Per-row per-platform provenance
ROW_PLATFORM_SOURCE: Dict[object, Dict[str, str]] = {}
# Per-row resolved username hints (platform → handle string, e.g. "johndoe123")
ROW_USERNAME_HINTS: Dict[object, Dict[str, str]] = {}

PLATFORMS: Dict[str, List[str]] = {
    "Facebook":  ["facebook.com"],
    "Instagram": ["instagram.com"],
    "X":         ["x.com", "twitter.com"],
    "TikTok":    ["tiktok.com"],
    "YouTube":   ["youtube.com"],
}

PLATFORM_CONF_COLUMNS: Dict[str, str] = {p: f"{p} Confidence" for p in PLATFORMS}


# ─────────────────────────────────────────────
#  UTILITY HELPERS
# ─────────────────────────────────────────────

def is_first_name_only(talent: str) -> bool:
    parts = re.sub(r"\s+", " ", (talent or "").strip()).split()
    return len(parts) == 1 and bool(parts[0])


def _find_column(raw: pd.DataFrame, *candidates: str) -> Optional[str]:
    cmap = {str(c).strip().lower(): c for c in raw.columns}
    for cand in candidates:
        if cand.lower() in cmap:
            return cmap[cand.lower()]
    return None


def _slug_chars(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


# ─────────────────────────────────────────────
#  NAME AMBIGUITY  (NEW)
# ─────────────────────────────────────────────

# Very common first names that routinely cause namesake collisions
_COMMON_FIRST_NAMES = {
    "andrea", "jessica", "jennifer", "ashley", "brittany", "britney",
    "samantha", "amanda", "sarah", "emily", "emma", "olivia", "megan",
    "michael", "james", "john", "david", "robert", "william", "daniel",
    "matthew", "chris", "jason", "kevin", "ryan", "brian", "tyler",
    "alex", "jordan", "taylor", "morgan", "charlie", "casey", "drew",
}

def get_name_ambiguity_level(talent: str) -> str:
    """
    Return 'high', 'medium', or 'low'.
    High ambiguity → raise confidence threshold by +0.10.
    Medium → raise by +0.05.
    Low → use base threshold.
    """
    parts = re.sub(r"\s+", " ", (talent or "").strip()).lower().split()
    if len(parts) == 1:
        return "high"   # single name: Andrea, Prince, etc.
    if len(parts) == 2:
        first = parts[0]
        if first in _COMMON_FIRST_NAMES:
            return "medium"
    return "low"


def _effective_min_confidence(talent: str) -> float:
    level = get_name_ambiguity_level(talent)
    if level == "high":
        return min(0.92, MIN_CONFIDENCE_EMIT + 0.10)
    if level == "medium":
        return min(0.85, MIN_CONFIDENCE_EMIT + 0.05)
    return MIN_CONFIDENCE_EMIT


# ─────────────────────────────────────────────
#  METADATA HELPERS
# ─────────────────────────────────────────────

def extract_search_keywords(title_category: str, title_sub_category: str) -> str:
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
    text = re.sub(
        r"(?i)\b(talent type|gender|talent subtype|publication type)\s*-\s*",
        " ", text,
    )
    text = re.sub(r"\s+", " ", text).strip()
    words = text.split()
    return " ".join(words[:14])[:140].strip()


def parse_entity_expectations(title_category: str, title_sub_category: str) -> Dict[str, bool]:
    blob = f"{title_category or ''} {title_sub_category or ''}".lower()
    return {
        "expects_male":       bool(re.search(r"gender\s*-\s*man\b", blob)),
        "expects_female":     bool(re.search(r"gender\s*-\s*woman\b", blob)),
        "expects_athlete":    bool(
            re.search(r"\bathlete\b", blob)
            or re.search(r"\bbasketball\b", blob)
            or re.search(r"\bfootball\b", blob)
            or "sport" in blob
        ),
        "expects_basketball": "basketball" in blob,
        "expects_musician":   bool(
            re.search(r"\bmusician\b|\bsinger\b|\brap(per)?\b|\bartist\b|\bband\b", blob)
        ),
        "expects_journalist": bool(
            re.search(r"\bjournalist\b|\bnews\b|\banchor\b|\breporter\b|\bhost\b|\bmedia\b", blob)
        ),
        "expects_executive":  bool(
            re.search(r"\bceo\b|\bcfo\b|\bcoo\b|\bexecutive\b|\bfounder\b|\bbusiness\b", blob)
        ),
        "expects_actor":      bool(
            re.search(r"\bactor\b|\bactress\b|\bfilm\b|\btelevision\b|\btv star\b", blob)
        ),
        "expects_politician": bool(
            re.search(r"\bpolitician\b|\bsenator\b|\bcongressman\b|\bgovernor\b|\bpresident\b", blob)
        ),
    }


def _metadata_tokens(search_keywords: str) -> List[str]:
    if not search_keywords:
        return []
    parts = re.split(r"[^\w]+", search_keywords.lower())
    stop = {"the", "and", "for", "type", "talent", "gender", "subtype", "publication", "network", "man", "woman"}
    return [p for p in parts if p and p not in stop and len(p) > 2]


# ─────────────────────────────────────────────
#  CANDIDATE SIGNALS  (NEW)
# ─────────────────────────────────────────────

_FOLLOWER_RE = re.compile(
    r"([\d,\.]+)\s*[KkMmBb]?\s*(?:followers|subscribers|fans)", re.I
)

def _parse_follower_count(text: str) -> Optional[float]:
    m = _FOLLOWER_RE.search(text or "")
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    try:
        val = float(raw)
    except ValueError:
        return None
    lower_full = m.group(0).lower()
    if "b" in lower_full:
        val *= 1_000_000_000
    elif "m" in lower_full:
        val *= 1_000_000
    elif "k" in lower_full:
        val *= 1_000
    return val


def build_candidate_signals(talent: str, candidate: dict, platform: str) -> dict:
    """
    Pre-compute deterministic evidence signals for one candidate.
    Passed alongside the raw candidate to the AI so it can reason directly
    from structured facts rather than re-deriving them.
    """
    title    = (candidate.get("title")   or "").strip()
    snippet  = (candidate.get("snippet") or "").strip()
    link     = (candidate.get("link")    or "").strip()
    blob     = f"{title} {snippet}".lower()
    path     = urlparse(link).path if link else ""
    slug     = _slug_chars(path)
    t_slug   = _slug_chars(talent)

    # --- Name presence ---
    name_parts = re.sub(r"\s+", " ", talent).lower().split()
    name_tokens_in_url    = [p for p in name_parts if len(p) >= 3 and _slug_chars(p) in slug]
    name_tokens_in_title  = [p for p in name_parts if p in title.lower()]
    name_tokens_in_snippet= [p for p in name_parts if p in snippet.lower()]

    # --- Full name in URL slug ---
    full_name_in_url = len(t_slug) >= 5 and t_slug[:min(8, len(t_slug))] in slug

    # --- Verification signals ---
    verification_signals = []
    if "official" in blob:
        verification_signals.append("official")
    if "verified" in blob or "✓" in title or "✔" in title:
        verification_signals.append("verified")
    if "blue check" in blob or "checkmark" in blob:
        verification_signals.append("blue_check")

    # --- Follower count ---
    follower_count = _parse_follower_count(blob)

    # --- Profession signals in snippet ---
    PROFESSION_MARKERS = [
        "realtor", "real estate", "mortgage", "listing agent",
        "basketball", "nba", "wnba", "athlete", "ncaa", "espn",
        "singer", "musician", "rapper", "artist", "album",
        "journalist", "reporter", "anchor", "news anchor",
        "actor", "actress", "film", "director",
        "ceo", "founder", "executive", "entrepreneur",
        "senator", "governor", "congressman", "politician",
        "author", "writer", "novelist",
        "doctor", "physician", "surgeon", "dentist", "lawyer", "attorney",
        "digital creator", "content creator", "influencer",
    ]
    profession_signals = [p for p in PROFESSION_MARKERS if p in blob]

    # --- URL depth (1 = likely profile) ---
    url_depth = len([s for s in path.strip("/").split("/") if s])

    # --- Generic handle penalty ---
    # Handles shorter than 4 chars or all-digit are suspicious
    handle_match = re.search(r"tiktok\.com/@(\w+)", link.lower())
    if not handle_match:
        handle_match = re.search(r"instagram\.com/(\w+)", link.lower())
    if not handle_match:
        handle_match = re.search(r"(?:x|twitter)\.com/(\w+)", link.lower())
    handle = handle_match.group(1) if handle_match else ""
    handle_suspicious = bool(handle and (len(handle) <= 3 or handle.isdigit()))

    return {
        "name_tokens_in_url":     name_tokens_in_url,
        "name_tokens_in_title":   name_tokens_in_title,
        "name_tokens_in_snippet": name_tokens_in_snippet,
        "full_name_in_url":       full_name_in_url,
        "verification_signals":   verification_signals,
        "follower_count":         follower_count,
        "profession_signals":     profession_signals,
        "url_depth":              url_depth,
        "handle":                 handle,
        "handle_suspicious":      handle_suspicious,
        "is_valid_profile_url":   is_valid_profile_url(link, platform),
    }


# ─────────────────────────────────────────────
#  CATEGORY DISAMBIGUATION CONTEXT  (NEW)
# ─────────────────────────────────────────────

def get_category_disambiguation_context(
    title_category: str, title_sub_category: str
) -> str:
    """
    Return a category-specific disambiguation instruction block for the AI prompt.
    This tells the model exactly what signals confirm vs contradict the expected identity.
    """
    exp = parse_entity_expectations(title_category, title_sub_category)
    lines: List[str] = []

    if exp["expects_athlete"] or exp["expects_basketball"]:
        sport = "basketball" if exp["expects_basketball"] else "sports/athletics"
        lines += [
            f"CATEGORY: This person is a {sport} ATHLETE.",
            "CONFIRM if: snippet/title mentions basketball, NBA, WNBA, NCAA, sports, hoops, draft, team name.",
            "REJECT if: snippet/title mentions 'realtor', 'real estate', 'mortgage', 'listing agent', 'homes for sale', 'digital creator' (without sports context).",
            "REJECT if: the profile clearly depicts a different profession (e.g. financial advisor, fitness trainer unrelated to sports).",
        ]
        if exp["expects_male"]:
            lines.append(
                "REJECT if: the profile clearly belongs to a woman when we expect a male athlete (check pronouns, name, bio)."
            )

    if exp["expects_musician"]:
        lines += [
            "CATEGORY: This person is a MUSICIAN / ARTIST.",
            "CONFIRM if: mentions music, songs, album, tour, label, artist, rapper, singer, band.",
            "REJECT if: the profile is clearly a business, brand, or unrelated individual with the same name.",
            "REJECT if: the profile is a tribute act, cover band, or fan page (look for 'tribute', 'cover', 'fan', 'unofficial').",
        ]

    if exp["expects_journalist"]:
        lines += [
            "CATEGORY: This person is a JOURNALIST / MEDIA HOST / NEWS ANCHOR.",
            "CONFIRM if: mentions journalism, news, reporting, broadcasting, anchor, host, network name (CNN, MSNBC, Fox, etc.).",
            "REJECT if: this is clearly a personal trainer, realtor, or unrelated person with the same name.",
        ]

    if exp["expects_actor"]:
        lines += [
            "CATEGORY: This person is an ACTOR / ACTRESS.",
            "CONFIRM if: mentions film, TV, show, movie, series, screen, Broadway, IMDb.",
            "REJECT if: the profile is a different entertainment professional (musician only) or fan account.",
        ]

    if exp["expects_executive"]:
        lines += [
            "CATEGORY: This person is a BUSINESS EXECUTIVE / FOUNDER / CEO.",
            "CONFIRM if: mentions company, startup, CEO, founder, entrepreneur, board, leadership.",
            "REJECT if: the profile is a personal creator account or unrelated individual.",
        ]

    if exp["expects_politician"]:
        lines += [
            "CATEGORY: This person is a POLITICIAN / PUBLIC OFFICIAL.",
            "CONFIRM if: mentions senator, congressman, governor, mayor, representative, campaign, district.",
            "REJECT if: the profile is a fan/parody account or unrelated public figure.",
        ]

    if not lines:
        lines = [
            "No specific category metadata available.",
            "Use name matching and profile authenticity signals (verified, official, follower count) to disambiguate.",
        ]

    return "\n".join(lines)


# ─────────────────────────────────────────────
#  ENTITY REJECTION  (EXPANDED)
# ─────────────────────────────────────────────

def entity_profile_rejected(
    talent: str,
    title_category: str,
    title_sub_category: str,
    candidate: Optional[dict],
) -> Tuple[bool, str]:
    """
    Reject Serper candidates that clearly contradict Excel metadata.
    Blank > wrong. Expanded to cover more categories.
    """
    if not candidate:
        return False, ""

    title   = (candidate.get("title")   or "")
    snippet = (candidate.get("snippet") or "")
    link    = (candidate.get("link")    or "")
    blob    = f"{title} {snippet}".lower()
    exp     = parse_entity_expectations(title_category, title_sub_category)

    # ── shared markers ──
    sport_markers = (
        "basketball", "nba", "wnba", "ncaa", "college basketball",
        "draft", "athlete", "espn", "sport", "point guard",
        "shooting guard", "forward", "center", "hoops", "nba draft",
        "football", "nfl", "soccer", "mls", "baseball", "mlb",
    )
    sport_hit = any(m in blob for m in sport_markers)

    non_sport_professions = (
        "realtor", "real estate", "mortgage", "homes realty",
        "florida homes", "digital creator", "realtor sales",
        "realty & mortgage", "realty and mortgage",
        "listing agent", "homes for sale", "property management",
    )
    non_sport_hit = any(m in blob for m in non_sport_professions)

    # ── Athlete ──
    if exp["expects_male"] and (exp["expects_athlete"] or exp["expects_basketball"]):
        if non_sport_hit and not sport_hit:
            return (
                True,
                "Metadata = male athlete; result is realtor/creator/real-estate professional.",
            )
        # Obvious female-named pages when we expect a male athlete
        female_hits = (
            "bobbie ", " bobbie", "brittany ", "britney ", "jessica ",
            "samantha ", "miss ", " mrs ", "she is ", "she's ", "her ",
        )
        if any(x in blob for x in female_hits) and not sport_hit:
            return (
                True,
                "Profile text signals a different (female) person; talent is a male athlete.",
            )

    if exp["expects_female"] and exp["expects_athlete"] and non_sport_hit and not sport_hit:
        male_lean = (" mr ", "his ", "his own", "father", "husband")
        if any(x in blob for x in male_lean) and "woman" not in blob:
            return (
                True,
                "Metadata = female athlete; result appears to be an unrelated male professional.",
            )

    # ── Musician ──
    if exp["expects_musician"]:
        non_music = (
            "realtor", "real estate", "lawyer", "attorney",
            "doctor", "physician", "financial advisor",
        )
        music_confirm = ("music", "artist", "singer", "rapper", "album", "tour", "label", "song")
        if any(m in blob for m in non_music) and not any(m in blob for m in music_confirm):
            return (
                True,
                "Metadata = musician; result is clearly a non-music professional.",
            )
        if any(x in blob for x in ("tribute", "tribute band", "fan page", "unofficial")):
            return True, "Fan/tribute/unofficial page — not the artist's own profile."

    # ── Journalist / News Anchor ──
    if exp["expects_journalist"]:
        non_media = ("realtor", "real estate", "fitness trainer", "personal trainer", "chef")
        media_confirm = ("news", "journalist", "anchor", "reporter", "host", "broadcasting", "media")
        if any(m in blob for m in non_media) and not any(m in blob for m in media_confirm):
            return True, "Metadata = journalist/anchor; result is a non-media professional."

    # ── Generic: news articles about the person are not their profile ──
    article_signals = (
        " - wikipedia", "wikipedia.org", "imdb.com", "biography",
        "interviews", "profile of ", "article about", "story of",
        " - espn.com", " | espn", " - bleacher report",
    )
    if any(x in (link + blob).lower() for x in article_signals):
        return True, "Result appears to be an editorial article or wiki page, not a social profile."

    return False, ""


# ─────────────────────────────────────────────
#  CANDIDATE RANKING  (EXPANDED)
# ─────────────────────────────────────────────

def candidate_rank_score(
    talent: str,
    c: dict,
    search_keywords: str,
    title_category: str = "",
    title_sub_category: str = "",
    username_hints: Optional[Dict[str, str]] = None,
    platform: str = "",
) -> float:
    title   = (c.get("title")   or "").lower()
    snippet = (c.get("snippet") or "").lower()
    link    = (c.get("link")    or "").lower()
    t       = re.sub(r"\s+", " ", (talent or "").strip()).lower()
    score   = 0.0

    # ── Authenticity signals ──
    if "official" in title or "official" in snippet:
        score += 4.0
    if "verified" in title or "verified" in snippet or "✓" in (c.get("title") or ""):
        score += 3.0

    # ── Name match ──
    if t and t in title:
        score += 3.5
    if t and t in snippet:
        score += 2.0

    # ── URL slug alignment (stronger weight) ──
    path_slug = _slug_chars(urlparse(link).path)
    name_slug = _slug_chars(talent)
    if name_slug and len(name_slug) >= 5 and name_slug[:min(8, len(name_slug))] in path_slug:
        score += 4.0  # strong: full-name prefix in URL
    else:
        for part in re.sub(r"\s+", " ", (talent or "").strip()).lower().split():
            sp = _slug_chars(part)
            if len(sp) >= 4 and sp in path_slug:
                score += 1.5

    # ── Follower / subscriber signal ──
    follower_count = _parse_follower_count(f"{title} {snippet}")
    if follower_count:
        if follower_count >= 1_000_000:
            score += 3.0
        elif follower_count >= 100_000:
            score += 2.0
        elif follower_count >= 10_000:
            score += 1.0

    # ── Metadata keyword alignment ──
    for token in _metadata_tokens(search_keywords):
        if len(token) < 3:
            continue
        if token in title or token in snippet:
            score += 1.5

    # ── Username hint bonus (cross-platform consistency) ──
    if username_hints and platform:
        for src_plat, hint in username_hints.items():
            if src_plat == platform or not hint:
                continue
            if _slug_chars(hint) in path_slug:
                score += 5.0   # exact handle match from another platform is very strong
                break

    # ── Generic handle penalty ──
    segs = [s for s in urlparse(link).path.strip("/").split("/") if s]
    if segs:
        handle = segs[-1].lstrip("@")
        if len(handle) <= 2 or handle.isdigit():
            score -= 3.0

    # ── Hard entity rejection ──
    rej, _ = entity_profile_rejected(talent, title_category, title_sub_category, c)
    if rej:
        score -= 35.0

    return score


# ─────────────────────────────────────────────
#  QUERY BUILDING  (EXPANDED)
# ─────────────────────────────────────────────

def build_queries(
    talent: str,
    platform: str,
    domains: List[str],
    search_keywords: str,
    title_category: str = "",
    title_sub_category: str = "",
    username_hints: Optional[Dict[str, str]] = None,
) -> List[str]:
    kw  = (search_keywords or "").strip()
    exp = parse_entity_expectations(title_category, title_sub_category)
    queries: List[str] = []

    # ── Username hint queries — highest-value; run first ──
    if username_hints:
        for src_plat, hint in username_hints.items():
            if src_plat == platform or not hint:
                continue
            for domain in domains:
                queries.append(f'site:{domain} "@{hint}"')
                queries.append(f'site:{domain} "{hint}"')
            queries.append(f'"{hint}" {platform}')
            break   # one hint is enough to add targeted queries

    # ── Sport-first queries (basketball namesake reduction) ──
    if exp["expects_male"] and exp["expects_basketball"]:
        for domain in domains:
            queries.append(f'site:{domain} "{talent}" basketball')
            queries.append(f'site:{domain} "{talent}" basketball player')
            queries.append(f'site:{domain} "{talent}" NCAA basketball')

    # ── Journalist / anchor queries ──
    if exp["expects_journalist"]:
        for domain in domains:
            queries.append(f'site:{domain} "{talent}" journalist anchor')
            queries.append(f'site:{domain} "{talent}" news host')

    # ── Musician queries ──
    if exp["expects_musician"]:
        for domain in domains:
            queries.append(f'site:{domain} "{talent}" music artist')
            queries.append(f'site:{domain} "{talent}" official artist')

    # ── Standard domain-scoped queries ──
    for domain in domains:
        queries.append(f'site:{domain} "{talent}" official')
        queries.append(f'site:{domain} "{talent}" verified')
        queries.append(f'site:{domain} "{talent}"')
        if kw:
            queries.append(f'site:{domain} "{talent}" {kw} official')
            queries.append(f'site:{domain} "{talent}" {kw}')

    # ── Fallback web queries ──
    queries.append(f'"{talent}" {platform} official')
    queries.append(f'"{talent}" {platform}')
    if kw:
        queries.append(f'"{talent}" {kw} {platform} official')
        queries.append(f'"{talent}" {kw} {platform}')

    # ── De-dupe preserving order ──
    seen: set = set()
    unique: List[str] = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            unique.append(q)
    return unique


# ─────────────────────────────────────────────
#  DATA LOADING
# ─────────────────────────────────────────────

def _default_talent_table() -> pd.DataFrame:
    n = len(talent_names)
    data: Dict[str, List] = {
        "Talent Name":        list(talent_names),
        "title_category":     [""] * n,
        "title_sub_category": [""] * n,
    }
    for p in PLATFORMS:
        data[p] = [""] * n
    for c in PLATFORM_CONF_COLUMNS.values():
        data[c] = [float("nan")] * n
    data["Confidence"] = [float("nan")] * n
    data["Source"]     = [""] * n
    return pd.DataFrame(data)


def load_talent_table_from_path(excel_path: Path) -> pd.DataFrame:
    excel_path = Path(excel_path)
    if not excel_path.is_file():
        raise ValueError(f"File not found: {excel_path}")
    suffix = excel_path.suffix.lower()
    try:
        raw = pd.read_csv(excel_path) if suffix == ".csv" else pd.read_excel(excel_path)
    except Exception as exc:
        raise ValueError(f"Could not read spreadsheet: {exc}") from exc
    if raw.empty:
        raise ValueError("The file has no rows.")

    name_col = _find_column(raw, "Talent Name", "Talent", "title", "Title", "Name")
    if name_col is None:
        name_col = raw.columns[0]
    cat_col = _find_column(raw, "title_category", "de_category", "category", "Title Category")
    sub_col = _find_column(raw, "title_sub_category", "sub_category", "Title Sub Category", "subtitle")

    names_list, cat_list, sub_list = [], [], []
    for i in range(len(raw)):
        name = str(raw.iloc[i][name_col]).strip()
        if not name or name.lower() == "nan":
            continue
        names_list.append(name)
        c = raw.iloc[i][cat_col] if cat_col else ""
        s = raw.iloc[i][sub_col] if sub_col else ""
        cat_list.append("" if (isinstance(c, float) and pd.isna(c)) else str(c).strip())
        sub_list.append("" if (isinstance(s, float) and pd.isna(s)) else str(s).strip())

    if not names_list:
        raise ValueError("No valid talent names found.")
    n = len(names_list)
    out: Dict[str, List] = {
        "Talent Name":        names_list,
        "title_category":     cat_list,
        "title_sub_category": sub_list,
    }
    for p in PLATFORMS:
        out[p] = [""] * n
    for c in PLATFORM_CONF_COLUMNS.values():
        out[c] = [float("nan")] * n
    out["Confidence"] = [float("nan")] * n
    out["Source"]     = [""] * n
    return pd.DataFrame(out)


def load_talent_table() -> pd.DataFrame:
    if not TEST_BRANDS_PATH.exists():
        return _default_talent_table()
    try:
        return load_talent_table_from_path(TEST_BRANDS_PATH)
    except ValueError as exc:
        print(f"[WARN] {exc}. Using default talent_names.")
        return _default_talent_table()


def build_talent_df(names: List[str], platforms: List[str]) -> pd.DataFrame:
    """Legacy helper: names only, no metadata columns."""
    data: Dict[str, List] = {"Talent Name": names}
    for p in platforms:
        data[p] = [""] * len(names)
    for p in platforms:
        data[f"{p} Confidence"] = [float("nan")] * len(names)
    data["title_category"]     = [""] * len(names)
    data["title_sub_category"] = [""] * len(names)
    data["Confidence"]         = [float("nan")] * len(names)
    data["Source"]             = [""] * len(names)
    return pd.DataFrame(data)


# ─────────────────────────────────────────────
#  URL VALIDATION & NORMALISATION
# ─────────────────────────────────────────────

def is_valid_profile_url(link: str, platform: str) -> bool:
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
        if any(x in full for x in ("/watch", "/shorts/", "/playlist", "/results",
                                    "/live/", "/feed/", "/attribution_link")):
            return False
        return "/@" in full or "/channel/" in full or "/c/" in full or "/user/" in full

    if platform == "X":
        if "x.com" not in host and "twitter.com" not in host:
            return False
        if "/status/" in full or "/i/" in full or "/intent/" in full or "/search" in full:
            return False
        segs = [s for s in path.strip("/").split("/") if s]
        return len(segs) == 1

    if platform == "TikTok":
        if "tiktok.com" not in host:
            return False
        if any(x in full for x in ("/video/", "/tag/", "/music/", "/discover", "/foryou")):
            return False
        return bool(re.search(r"tiktok\.com/@[^/]+/?$", full))

    return False


def normalize_profile_url(url: str, platform: str) -> str:
    if not url or not isinstance(url, str):
        return ""
    u = url.strip()
    if platform == "YouTube":
        u = u.replace("://m.youtube.com", "://www.youtube.com")
        u = u.replace("://music.youtube.com", "://www.youtube.com")
        if "youtube.com" in u and "www." not in urlparse(u).netloc and "m." not in urlparse(u).netloc:
            u = u.replace("://youtube.com", "://www.youtube.com")
    return u.rstrip("/")


def talent_url_aligned(talent: str, link: str) -> bool:
    t = _slug_chars(talent)
    if len(t) < 4:
        return False
    path_compact = _slug_chars(urlparse(link).path)
    if len(t) >= 6 and t[:min(8, len(t))] in path_compact:
        return True
    for part in re.sub(r"\s+", " ", (talent or "").strip()).lower().split():
        if len(part) < 3:
            continue
        sp = _slug_chars(part)
        if len(sp) >= 5 and sp in path_compact:
            return True
    return False


def first_valid_profile_link(candidates: List[dict], platform: str) -> str:
    for item in candidates:
        if is_valid_profile_url(item.get("link", ""), platform):
            return normalize_profile_url(item["link"], platform)
    return ""


def sort_candidates_for_ai(
    talent: str,
    candidates: List[dict],
    search_keywords: str,
    title_category: str = "",
    title_sub_category: str = "",
    username_hints: Optional[Dict[str, str]] = None,
    platform: str = "",
) -> List[dict]:
    return sorted(
        candidates,
        key=lambda c: -candidate_rank_score(
            talent, c, search_keywords, title_category, title_sub_category,
            username_hints=username_hints, platform=platform,
        ),
    )


# ─────────────────────────────────────────────
#  USERNAME HINT EXTRACTION  (NEW)
# ─────────────────────────────────────────────

def extract_username_hints(resolved_links: Dict[str, str]) -> Dict[str, str]:
    """
    From already-resolved platform URLs, extract the @handle/slug.
    e.g. instagram.com/johndoe123 → {"Instagram": "johndoe123"}
    Used to bias searches and rank on other platforms.
    """
    hints: Dict[str, str] = {}
    for plat, url in resolved_links.items():
        if not url:
            continue
        url = url.strip().rstrip("/")
        # TikTok: /@handle
        m = re.search(r"tiktok\.com/@([\w.]+)", url, re.I)
        if m:
            hints[plat] = m.group(1)
            continue
        # YouTube: /@handle or /c/handle or /channel/...
        m = re.search(r"youtube\.com/@([\w.]+)", url, re.I)
        if m:
            hints[plat] = m.group(1)
            continue
        m = re.search(r"youtube\.com/c/([\w.]+)", url, re.I)
        if m:
            hints[plat] = m.group(1)
            continue
        # Instagram, X, Facebook: domain/handle
        m = re.search(r"(?:instagram|twitter|x|facebook)\.com/(@?)([\w.]+)", url, re.I)
        if m:
            hints[plat] = m.group(2)
            continue
    return hints


# ─────────────────────────────────────────────
#  AI — MAIN SELECTION  (REWRITTEN)
# ─────────────────────────────────────────────

def ai_select_best_profile(
    talent: str,
    platform: str,
    candidates: List[dict],
    entity_category: str,
    entity_sub_category: str,
    search_keywords: str,
    username_hints: Optional[Dict[str, str]] = None,
) -> dict:
    """
    Two-phase AI selection:
      Phase 1 — Evaluate each candidate (ACCEPT / MAYBE / REJECT + reason).
      Phase 2 — Pick the single best from ACCEPT; fall to MAYBE only if no ACCEPT.
               If neither exists, return empty.

    Improvements over original:
    - Chain-of-thought instructions
    - Pre-computed signals passed per candidate
    - Category-specific disambiguation rules from get_category_disambiguation_context()
    - Stricter blank-preferred instructions
    - Username hints from other platforms as a high-confidence signal
    """
    if not candidates:
        return {"best_link": "", "confidence": 0.0, "reason": "No candidates provided."}

    cat_context = get_category_disambiguation_context(entity_category, entity_sub_category)

    # Enrich candidates with pre-computed signals
    enriched_candidates = []
    for c in candidates:
        signals = build_candidate_signals(talent, c, platform)
        enriched_candidates.append({
            "link":    c.get("link", ""),
            "title":   c.get("title", ""),
            "snippet": c.get("snippet", ""),
            "signals": signals,
        })

    # Username hints from other resolved platforms
    hint_lines = []
    if username_hints:
        for src_plat, hint in username_hints.items():
            hint_lines.append(
                f"  • On {src_plat} we already found @{hint} — prioritise candidates whose URL contains this handle."
            )
    hint_block = (
        "CROSS-PLATFORM HINTS (from other platforms already resolved for this talent):\n"
        + "\n".join(hint_lines)
        if hint_lines else ""
    )

    system_msg = (
        "You are an expert social media profile resolver working for a talent research firm. "
        "Your job is to identify the single official, active social media profile for a real public figure. "
        "\n\nCORE RULE: A blank cell is ALWAYS better than a wrong link. "
        "When uncertain, return empty string for best_link. "
        "\nNEVER select: posts, videos, reels, shorts, news articles, Wikipedia pages, fan pages, "
        "tribute accounts, brand pages for companies, or profiles that clearly belong to a different person."
    )

    user_msg = f"""
TALENT: "{talent}"
PLATFORM: {platform}
SEARCH KEYWORDS FROM METADATA: {search_keywords or "(none)"}

CATEGORY CONTEXT:
{cat_context}

{hint_block}

CANDIDATES (each has pre-computed signals to help you):
{json.dumps(enriched_candidates, indent=2, ensure_ascii=True)}

INSTRUCTIONS:
Step 1 — For EACH candidate, classify it as:
  ACCEPT  → very likely the correct official profile for this talent
  MAYBE   → possible but uncertain
  REJECT  → wrong person, fan page, article, or content URL

Use these signals in order of importance:
  1. signals.name_tokens_in_url and signals.full_name_in_url  (strongest identifier)
  2. signals.verification_signals (official, verified, blue_check)
  3. Cross-platform username hints (if handle from another platform appears in URL)
  4. signals.profession_signals matching the expected category
  5. signals.follower_count (higher = more credible public figure)
  6. signals.name_tokens_in_title and signals.name_tokens_in_snippet

Step 2 — From all ACCEPT candidates, choose the one with the most signals.
  If no ACCEPT, choose from MAYBE only if confidence ≥ 0.75.
  If no suitable candidate: return best_link="" and confidence<0.40.

BLANK RULES (return best_link="" if any of these apply):
  • All candidates appear to be the wrong person
  • The talent name is very common (e.g. "Andrea", "Jessica") and no candidate clearly confirms identity
  • The best candidate's profession_signals conflict with the expected category (e.g. realtor for an athlete)
  • The best candidate is clearly a fan/tribute/unofficial page
  • You cannot distinguish between 2+ legitimate people with the same name

OUTPUT FORMAT — strict JSON only, no markdown, no extra keys:
{{
  "phase1_evaluation": [
    {{"link": "...", "verdict": "ACCEPT|MAYBE|REJECT", "reason": "short reason"}}
  ],
  "best_link": "URL or empty string",
  "confidence": 0.0,
  "reason": "one sentence"
}}
"""

    body = {
        "model": OPENAI_CHAT_MODEL,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user",   "content": user_msg},
        ],
    }
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers=headers, json=body, timeout=60,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    parsed  = _extract_json_obj(content)

    best_link  = str(parsed.get("best_link", "") or "").strip()
    confidence = float(parsed.get("confidence", 0.0))
    reason     = str(parsed.get("reason", "") or "").strip()
    confidence = max(0.0, min(1.0, confidence))

    # Log phase1 evaluation for debugging
    for ev in (parsed.get("phase1_evaluation") or []):
        verdict = ev.get("verdict", "?")
        link_ev = (ev.get("link") or "")[:80]
        print(f"  [PHASE1] {verdict:6s} | {link_ev} — {ev.get('reason','')}")

    # Safety: if AI returned something outside the candidate list, discard
    candidate_links = {normalize_profile_url(c.get("link", ""), platform).rstrip("/")
                       for c in candidates}
    if best_link:
        norm = normalize_profile_url(best_link, platform).rstrip("/")
        if norm not in candidate_links:
            print(f"[WARN] AI returned a link not in candidates — discarding: {best_link}")
            best_link  = ""
            confidence = min(confidence, 0.30)
            reason     = f"AI link not in candidate set (discarded). {reason}"

    return {"best_link": best_link, "confidence": confidence, "reason": reason or "No confident match."}


# ─────────────────────────────────────────────
#  AI — VERIFICATION PASS  (NEW)
# ─────────────────────────────────────────────

def ai_verify_selected_link(
    talent: str,
    platform: str,
    link: str,
    title: str,
    snippet: str,
    entity_category: str,
    entity_sub_category: str,
    search_keywords: str,
) -> Tuple[bool, float, str]:
    """
    Quick second AI call: "Does this specific URL definitively belong to [talent]?"
    Returns (verified: bool, adjusted_confidence: float, reason: str).
    If verified=False, the caller should blank the result.
    """
    cat_context = get_category_disambiguation_context(entity_category, entity_sub_category)
    signals = build_candidate_signals(talent, {"link": link, "title": title, "snippet": snippet}, platform)

    system_msg = (
        "You are a fact-checker verifying whether a specific social media URL belongs "
        "to a specific public figure. Answer with strict JSON only."
    )
    user_msg = f"""
TALENT: "{talent}"
PLATFORM: {platform}
CATEGORY CONTEXT: {cat_context}
SEARCH KEYWORDS: {search_keywords or "(none)"}

URL TO VERIFY: {link}
PAGE TITLE:    {title}
SNIPPET:       {snippet}
PRE-COMPUTED SIGNALS: {json.dumps(signals, ensure_ascii=True)}

QUESTION: Does this URL clearly and definitively belong to the talent named above on {platform}?

Answer YES only if you are confident this is their real, official profile.
Answer NO if:
  • This is clearly a different person
  • This is a fan/tribute/unofficial page
  • This is a news article or Wikipedia page
  • There is not enough evidence to confirm identity

Output strict JSON only:
{{"verified": true/false, "confidence": 0.0, "reason": "one sentence"}}
"""
    body = {
        "model": OPENAI_CHAT_MODEL,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user",   "content": user_msg},
        ],
    }
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers, json=body, timeout=40,
        )
        response.raise_for_status()
        parsed = _extract_json_obj(response.json()["choices"][0]["message"]["content"])
        verified   = bool(parsed.get("verified", False))
        conf       = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
        verify_rsn = str(parsed.get("reason", "")).strip()
        return verified, conf, verify_rsn
    except Exception as exc:
        print(f"[WARN] Verify call failed: {exc}")
        return True, 0.0, f"Verify skipped ({exc})"


# ─────────────────────────────────────────────
#  EMISSION GATE  (STRICTER DYNAMIC THRESHOLD)
# ─────────────────────────────────────────────

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
    """
    Final gate before writing to Excel.
    Uses a dynamic threshold based on name ambiguity — common / single names
    require higher confidence before we emit anything.
    """
    effective_min = _effective_min_confidence(talent)

    if not selected or selected == "Not Found":
        return "", confidence, reason or "No selection."

    selected = normalize_profile_url(selected, platform)
    if not is_valid_profile_url(selected, platform):
        return "", 0.0, "Rejected: not a valid profile/channel URL."

    if emit_candidate:
        rej, why = entity_profile_rejected(talent, title_category, title_sub_category, emit_candidate)
        if rej:
            return "", min(confidence, 0.12), why

    if confidence >= effective_min:
        return selected, confidence, reason

    # Fallback: strong deterministic rank + URL name alignment
    if top_candidate is not None:
        rej_fb, rej_msg = entity_profile_rejected(talent, title_category, title_sub_category, top_candidate)
        if rej_fb:
            return "", confidence, f"Omitted: {rej_msg}"
        rs   = candidate_rank_score(talent, top_candidate, search_keywords, title_category, title_sub_category)
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

    return "", confidence, f"Omitted (below {effective_min:.2f}): {reason}"


# ─────────────────────────────────────────────
#  SERPER SEARCH
# ─────────────────────────────────────────────

def serper_search(query: str, num_results: int = 10) -> List[dict]:
    url = "https://google.serper.dev/search"
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload = {"q": query, "num": max(1, min(num_results, 10))}
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        try:
            detail = response.json().get("message") or response.text
        except ValueError:
            detail = response.text
        raise RuntimeError(f"Serper search failed ({response.status_code}): {detail}") from exc
    data = response.json()
    return [
        {
            "title":   item.get("title", "") or "",
            "snippet": item.get("snippet", "") or "",
            "link":    item.get("link", "") or "",
        }
        for item in data.get("organic", [])
    ]


def _extract_json_obj(text: str) -> dict:
    if not text:
        raise ValueError("Empty OpenAI response.")
    start = text.find("{")
    end   = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in OpenAI response.")
    return json.loads(text[start: end + 1])


# ─────────────────────────────────────────────
#  HTML ENRICHMENT (unchanged)
# ─────────────────────────────────────────────

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
    found: set = set()
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
    for plat in PLATFORMS:
        if is_valid_profile_url(url, plat):
            return plat
    u = url.lower()
    if "linktr.ee/" in u or "linktree.com/" in u or "lnk.bio" in u or "beacons.ai" in u:
        return "__link_hub__"
    return None


def extract_social_links_from_page(page_url: str, source_platform: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    to_fetch = [page_url]
    if source_platform == "YouTube":
        base = page_url.split("?")[0].rstrip("/")
        if "/@" in base or "/channel/" in base or "/c/" in base or "/user/" in base:
            if "/about" not in base:
                to_fetch.append(base + "/about")
    hubs_fetched = 0
    seen_fetch: set = set()
    for u in to_fetch:
        u = u.strip()
        if not u or u in seen_fetch:
            continue
        seen_fetch.add(u)
        html = fetch_html(u)
        for raw in extract_urls_from_html(html):
            raw  = raw.strip().rstrip(".,);")
            plat = _platform_for_discovered_url(raw)
            if plat and plat != "__link_hub__" and plat not in out:
                out[plat] = normalize_profile_url(raw, plat)
            elif plat == "__link_hub__" and hubs_fetched < 3:
                hubs_fetched += 1
                for raw2 in extract_urls_from_html(fetch_html(raw)):
                    raw2 = raw2.strip().rstrip(".,);")
                    p2   = _platform_for_discovered_url(raw2)
                    if p2 and p2 != "__link_hub__" and p2 not in out:
                        out[p2] = normalize_profile_url(raw2, p2)
    return out


def enrich_row_from_anchor_profiles(df: pd.DataFrame, row_label: object) -> None:
    anchor_order = ["Instagram", "YouTube", "X", "Facebook", "TikTok"]
    confs = ROW_PLATFORM_CONFIDENCE.get(row_label, {})
    best_plat, best_url, best_c = None, "", 0.0
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
        if str(df.at[row_label, tgt] or "").strip():
            continue
        if not is_valid_profile_url(link, tgt):
            continue
        df.at[row_label, tgt] = link
        conf_value = round(min(0.93, best_c * 0.96), 3)
        ROW_PLATFORM_CONFIDENCE.setdefault(row_label, {})[tgt] = conf_value
        df.at[row_label, PLATFORM_CONF_COLUMNS[tgt]]           = conf_value
        ROW_PLATFORM_SOURCE.setdefault(row_label, {})[tgt]     = "bio_enrich"
        print(f"  + filled {tgt} from bio/link hub")
    _refresh_row_aggregate_confidence(df, row_label)


def _refresh_row_aggregate_confidence(df: pd.DataFrame, row_label: object) -> None:
    parts = [
        float(ROW_PLATFORM_CONFIDENCE.get(row_label, {}).get(p, 0.0))
        for p in PLATFORMS
        if str(df.at[row_label, p] or "").strip()
    ]
    if parts:
        df.at[row_label, "Confidence"] = round(sum(parts) / len(parts), 4)


def _refresh_row_source_cell(df: pd.DataFrame, row_label: object) -> None:
    parts = []
    for p in PLATFORMS:
        if not str(df.at[row_label, p] or "").strip():
            continue
        src = ROW_PLATFORM_SOURCE.get(row_label, {}).get(p, "")
        if src:
            parts.append(f"{p}:{src}")
    df.at[row_label, "Source"] = "; ".join(parts)


# ─────────────────────────────────────────────
#  SEARCH ONE PLATFORM  (with verify pass added)
# ─────────────────────────────────────────────

def search_one_platform(
    talent: str,
    platform: str,
    domains: List[str],
    title_category: str,
    title_sub_category: str,
    username_hints: Optional[Dict[str, str]] = None,
) -> Tuple[str, str, float, str]:
    search_keywords = extract_search_keywords(title_category, title_sub_category)
    all_candidates: List[dict] = []
    seen_links: set = set()

    queries = build_queries(
        talent, platform, domains, search_keywords,
        title_category, title_sub_category,
        username_hints=username_hints,
    )

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
            print(f"[WARN] Serper failed '{query}': {exc}")
            fatal_markers = (
                "not enough credits",
                "unauthorized",
                "invalid api key",
                "forbidden",
                "quota",
                "billing",
            )
            if any(marker in str(exc).lower() for marker in fatal_markers):
                raise RuntimeError(str(exc)) from exc
        if len(all_candidates) >= RESULTS_PER_QUERY * 2:
            break
        time.sleep(0.2)

    valid_candidates = [c for c in all_candidates if is_valid_profile_url(c.get("link", ""), platform)]
    valid_candidates = sort_candidates_for_ai(
        talent, valid_candidates, search_keywords, title_category, title_sub_category,
        username_hints=username_hints, platform=platform,
    )
    top_candidates = valid_candidates[:MAX_CANDIDATES_FOR_AI]

    ctx = f" | kw: {search_keywords}" if search_keywords else ""
    print(f"[INFO] {platform} | {talent}{ctx} -> {len(top_candidates)} profile-filtered candidates for AI")

    if not top_candidates:
        return platform, "", 0.0, "No valid profile/channel URLs in search results."

    top_candidate = top_candidates[0]
    fallback      = first_valid_profile_link(top_candidates, platform)

    try:
        ai_result  = ai_select_best_profile(
            talent, platform, top_candidates,
            title_category, title_sub_category, search_keywords,
            username_hints=username_hints,
        )
        selected   = ai_result["best_link"]
        confidence = ai_result["confidence"]
        reason     = ai_result["reason"]

        # ── If AI returned empty, try strong-rank fallback ──
        if not selected and fallback:
            rej_fb, _ = entity_profile_rejected(talent, title_category, title_sub_category, top_candidate)
            if not rej_fb:
                selected   = fallback
                confidence = min(confidence, 0.42)
                reason     = (reason or "") + " | AI empty; using top-ranked candidate."

        # ── If AI returned invalid URL, try fallback ──
        elif selected and not is_valid_profile_url(selected, platform):
            print(f"[WARN] AI returned non-profile URL; trying fallback.")
            rej_fb, _ = entity_profile_rejected(talent, title_category, title_sub_category, top_candidate)
            if fallback and not rej_fb:
                selected   = fallback
                confidence = min(confidence, 0.42)
                reason     = f"{reason} (AI URL invalid; fallback used)"
            else:
                selected   = ""
                confidence = 0.0

        # ── Hard entity check on the selected candidate ──
        emit_candidate: Optional[dict] = None
        if selected:
            sel_norm = normalize_profile_url(selected, platform).rstrip("/")
            cand     = next(
                (c for c in top_candidates
                 if normalize_profile_url(c.get("link", ""), platform).rstrip("/") == sel_norm),
                None,
            )
            emit_candidate = cand
            if cand:
                rej, why = entity_profile_rejected(talent, title_category, title_sub_category, cand)
                if rej:
                    print(f"[REJECT] {platform} | {talent} | {why}")
                    selected = ""; confidence = min(confidence, 0.15); reason = why
                    emit_candidate = None

        # ── Verification pass (only if confidence is meaningful) ──
        if selected and confidence >= 0.55:
            cand_for_verify = emit_candidate or top_candidate
            verified, verify_conf, verify_rsn = ai_verify_selected_link(
                talent, platform, selected,
                cand_for_verify.get("title", "") if cand_for_verify else "",
                cand_for_verify.get("snippet", "") if cand_for_verify else "",
                title_category, title_sub_category, search_keywords,
            )
            print(f"[VERIFY] {platform} | {talent} | verified={verified} conf={verify_conf:.2f} | {verify_rsn}")
            if not verified and verify_conf < AI_VERIFY_MIN_CONFIDENCE:
                # Verification failed decisively
                print(f"[VETO] Verify vetoed result for {platform} | {talent}")
                selected   = ""
                confidence = min(confidence, verify_conf)
                reason     = f"Verify pass failed: {verify_rsn}"
                emit_candidate = None
            elif verified and verify_conf > 0:
                # Blend confidence: average of selection + verify
                confidence = round((confidence + verify_conf) / 2, 4)
                reason     = f"{reason} [verify: {verify_rsn}]"

        # ── Final emission gate ──
        emit, conf_out, rsn_out = decide_emitted_link(
            talent, platform, selected or "", confidence, reason,
            top_candidate, search_keywords, title_category, title_sub_category,
            emit_candidate=emit_candidate,
        )
        return platform, emit, conf_out, rsn_out

    except Exception as exc:
        print(f"[ERROR] AI/Verify failed for {platform} | {talent}: {exc}")
        # Last-resort deterministic fallback
        if fallback:
            rej_fb, _ = entity_profile_rejected(talent, title_category, title_sub_category, top_candidate)
            if not rej_fb:
                rs = candidate_rank_score(
                    talent, top_candidate, search_keywords, title_category, title_sub_category
                )
                if rs >= MIN_RANK_SCORE_FOR_FALLBACK and talent_url_aligned(talent, fallback):
                    return (
                        platform,
                        normalize_profile_url(fallback, platform),
                        min(0.60, rs / 22.0),
                        f"AI error fallback (rank={rs:.1f}): {exc}",
                    )
        return platform, "", 0.0, f"AI error: {exc}"


# ─────────────────────────────────────────────
#  PROCESS ONE ROW
# ─────────────────────────────────────────────

def process_row(
    df: pd.DataFrame,
    row_label: object,
    progress_callback: Optional[Callable] = None,
) -> None:
    talent            = str(df.at[row_label, "Talent Name"] or "").strip()
    title_category    = str(df.at[row_label, "title_category"]    or "").strip()
    title_sub_category= str(df.at[row_label, "title_sub_category"] or "").strip()

    if not talent:
        return

    ambiguity = get_name_ambiguity_level(talent)
    print(f"\n{'='*65}")
    print(f"Processing: {talent}  [ambiguity={ambiguity}]")
    if title_category or title_sub_category:
        print(f"  Category: {title_category} | SubCategory: {title_sub_category}")

    # Resolve platforms one at a time (sequential for username-hint propagation)
    resolved_links: Dict[str, str] = {}
    for platform, domains in PLATFORMS.items():
        existing = str(df.at[row_label, platform] or "").strip()
        if existing:
            resolved_links[platform] = existing
            continue

        # Build username hints from what's been resolved so far
        username_hints = extract_username_hints(resolved_links)

        try:
            plat_out, link, confidence, reason = search_one_platform(
                talent, platform, domains,
                title_category, title_sub_category,
                username_hints=username_hints,
            )
        except Exception as exc:
            print(f"  [{platform}] UNEXPECTED ERROR: {exc}")
            link, confidence, reason = "", 0.0, str(exc)

        df.at[row_label, platform] = link
        ROW_PLATFORM_CONFIDENCE.setdefault(row_label, {})[platform] = confidence
        df.at[row_label, PLATFORM_CONF_COLUMNS[platform]] = confidence if link else float("nan")
        if link:
            ROW_PLATFORM_SOURCE.setdefault(row_label, {})[platform] = "search"
            resolved_links[platform] = link

        print(f"  [{platform}] {link or '(blank)'} (conf={confidence:.2f}) — {reason}")
        time.sleep(OPENAI_DELAY_SECONDS)

    # Enrich missing platforms from anchor bios / Linktree
    enrich_row_from_anchor_profiles(df, row_label)
    _refresh_row_aggregate_confidence(df, row_label)
    _refresh_row_source_cell(df, row_label)

    if progress_callback:
        progress_callback(talent)


# ─────────────────────────────────────────────
#  API PIPELINE WRAPPERS
# ─────────────────────────────────────────────

def _ensure_pipeline_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Keep uploaded/name-only dataframes compatible with the resolver pipeline."""
    for column in ("Talent Name", "title_category", "title_sub_category"):
        if column not in df.columns:
            df[column] = ""
    for platform in PLATFORMS:
        if platform not in df.columns:
            df[platform] = ""
        conf_column = PLATFORM_CONF_COLUMNS[platform]
        if conf_column not in df.columns:
            df[conf_column] = float("nan")
    if "Confidence" not in df.columns:
        df["Confidence"] = float("nan")
    if "Source" not in df.columns:
        df["Source"] = ""
    return df


def run_pipeline_on_dataframe(
    df: pd.DataFrame,
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> pd.DataFrame:
    """
    Run the social lookup pipeline on a prepared dataframe.
    progress receives (1-based index, total rows, talent name).
    """
    df = _ensure_pipeline_columns(df.copy())
    ROW_PLATFORM_CONFIDENCE.clear()
    ROW_PLATFORM_SOURCE.clear()
    ROW_USERNAME_HINTS.clear()

    total = len(df)
    print(f"Initialized talent dataframe with {total} row(s).")

    for i, row_label in enumerate(df.index, start=1):
        talent = str(df.at[row_label, "Talent Name"] or "").strip()
        if not talent:
            continue

        ROW_PLATFORM_CONFIDENCE[row_label] = {}
        ROW_PLATFORM_SOURCE[row_label] = {}
        for platform in PLATFORMS:
            if str(df.at[row_label, platform] or "").strip():
                ROW_PLATFORM_CONFIDENCE[row_label][platform] = 1.0
                ROW_PLATFORM_SOURCE[row_label][platform] = "input"
                df.at[row_label, PLATFORM_CONF_COLUMNS[platform]] = 1.0

        if progress:
            progress(i, total, talent)
        process_row(df, row_label)
        delay = random.uniform(*REQUEST_DELAY_BETWEEN_TALENTS)
        print(f"  [{i}/{total}] complete — sleeping {delay:.1f}s")
        time.sleep(delay)

    return df


def run_pipeline_for_names(
    names: List[str],
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> pd.DataFrame:
    """Build a dataframe from plain names and run the lookup pipeline."""
    clean = [str(name).strip() for name in names if name and str(name).strip()]
    if not clean:
        raise ValueError("At least one non-empty name is required.")
    df = build_talent_df(clean, list(PLATFORMS.keys()))
    return run_pipeline_on_dataframe(df, progress=progress)


def run_pipeline() -> pd.DataFrame:
    return run_pipeline_on_dataframe(load_talent_table())


# ─────────────────────────────────────────────
#  EXCEL OUTPUT WITH FORMATTING
# ─────────────────────────────────────────────

def save_results(df: pd.DataFrame, output_dir: Optional[Path] = None) -> Path:
    timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_dir = Path(output_dir) if output_dir is not None else Path(__file__).resolve().parent
    base_dir.mkdir(parents=True, exist_ok=True)
    output_path = base_dir / f"Talent_Social_Lookup_{timestamp}.xlsx"

    try:
        from openpyxl import load_workbook
        from openpyxl.styles import PatternFill, Font, Alignment
        from openpyxl.utils import get_column_letter
    except ImportError:
        df.to_excel(output_path, index=False)
        print(f"\n✅ Saved (no formatting): {output_path}")
        return output_path

    df.to_excel(output_path, index=False)
    wb = load_workbook(output_path)
    ws = wb.active

    # Header style
    header_fill = PatternFill("solid", fgColor="1F4E79")
    header_font = Font(bold=True, color="FFFFFF", size=10)
    for cell in ws[1]:
        cell.fill      = header_fill
        cell.font      = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Identify confidence columns
    conf_col_indices = {}
    for col_idx, cell in enumerate(ws[1], start=1):
        val = str(cell.value or "")
        for p in PLATFORMS:
            if val == PLATFORM_CONF_COLUMNS[p]:
                conf_col_indices[p] = col_idx

    # Row colouring
    low_conf_fill  = PatternFill("solid", fgColor="FFF2CC")  # yellow — risky
    warn_fill      = PatternFill("solid", fgColor="FCE4D6")  # light orange — first-name-only
    ok_fill        = PatternFill("solid", fgColor="E2EFDA")  # green — high confidence

    col_headers = [str(cell.value or "") for cell in ws[1]]

    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        talent_val = str(row[0].value or "")
        first_only = is_first_name_only(talent_val)

        # Find overall confidence value
        try:
            conf_idx    = col_headers.index("Confidence")
            overall_val = row[conf_idx].value
            overall_conf= float(overall_val) if overall_val not in (None, "") else None
        except (ValueError, TypeError):
            overall_conf = None

        for cell in row:
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            if first_only:
                cell.fill = warn_fill
            elif overall_conf is not None:
                if overall_conf >= 0.85:
                    cell.fill = ok_fill
                elif overall_conf < MIN_CONFIDENCE_EMIT:
                    cell.fill = low_conf_fill

    # Auto-width
    for col_idx in range(1, ws.max_column + 1):
        max_len = 0
        for row_cell in ws.iter_rows(min_col=col_idx, max_col=col_idx):
            for c in row_cell:
                try:
                    max_len = max(max_len, len(str(c.value or "")))
                except Exception:
                    pass
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 4, 45)

    # Freeze header row
    ws.freeze_panes = "A2"

    wb.save(output_path)
    print(f"\n✅ Saved: {output_path}")
    return output_path


def save_output(df: pd.DataFrame, output_dir: Optional[Path] = None) -> str:
    """API-compatible output helper used by api_server.py."""
    return str(save_results(df, output_dir=output_dir))


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main() -> None:
    if not SERPER_API_KEY:
        print("[ERROR] SERPER_API_KEY not set. Add it to .env")
        return
    if not OPENAI_API_KEY:
        print("[ERROR] OPENAI_API_KEY not set. Add it to .env")
        return

    df    = load_talent_table()
    total = len(df)
    print(f"Loaded {total} talent row(s).")

    for idx, row_label in enumerate(df.index):
        talent = str(df.at[row_label, "Talent Name"] or "").strip()
        if not talent:
            continue
        process_row(df, row_label)
        delay = random.uniform(*REQUEST_DELAY_BETWEEN_TALENTS)
        print(f"  [{idx+1}/{total}] complete — sleeping {delay:.1f}s")
        time.sleep(delay)

    save_results(df)


if __name__ == "__main__":
    main()