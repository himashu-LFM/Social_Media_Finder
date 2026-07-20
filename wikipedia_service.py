"""
wikipedia_service.py  —  Structured Wikipedia/Wikidata metadata extraction
==========================================================================

Given a talent name and (optionally) a Wikipedia URL, produce a small,
STRUCTURED identity record for the LLM verification engine.

Design rule (important):
    We NEVER send the full Wikipedia article to the LLM. We only extract a
    compact, structured metadata record (name, aliases, profession,
    nationality, birth year, known works, official website, short summary).

Built on top of the existing ``wikidata_lookup`` module (QID resolution,
entity fetch, social-property extraction) so we do not duplicate that logic.

Public interface:
    fetch_wiki_metadata(talent, wikipedia_url="", title_category="",
                        title_sub_category="") -> WikiMetadata

    WikiMetadata.social_links -> {platform: url}  # Wikidata-declared, trusted
    WikiMetadata.to_prompt_dict() -> dict          # compact dict for the LLM
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import wikidata_lookup as wd

# Reuse the shared HTTP session + timeout from wikidata_lookup so we keep a
# single set of headers / connection pool across the Wikipedia services.
_SESSION = wd._SESSION
TIMEOUT = wd.TIMEOUT

# Wikidata property ids we read for structured identity metadata.
_PROP_OCCUPATION = "P106"        # occupation(s)
_PROP_CITIZENSHIP = "P27"        # country of citizenship
_PROP_BIRTH_DATE = "P569"        # date of birth
_PROP_NOTABLE_WORK = "P800"      # notable work(s)
_PROP_OFFICIAL_SITE = "P856"     # official website
_PROP_NICKNAME = "P1449"         # nickname
_PROP_WORK_START = "P2031"       # work period (start)
_PROP_WORK_END = "P2032"         # work period (end)
_PROP_INSTANCE_OF = "P31"        # instance of (Q5 = human)
_QID_HUMAN = "Q5"
_PROP_PUB_DATE = "P577"          # publication/release date (films, games)
_PROP_START_TIME = "P580"        # start time (TV series premiere)

# Additional entity-valued properties, resolved to English labels in one batch.
# {prop: (field_name, max_values, single_valued)}
_ENTITY_PROPS: Dict[str, tuple] = {
    "P21":  ("gender", 1, True),        # sex or gender
    "P641": ("sports", 3, False),       # sport
    "P54":  ("teams", 8, False),        # member of sports team
    "P413": ("position", 3, False),     # position played / speciality
    "P69":  ("education", 4, False),    # educated at
    "P166": ("awards", 8, False),       # award received
    "P136": ("genres", 5, False),       # genre
    "P108": ("employers", 5, False),    # employer
    "P463": ("member_of", 5, False),    # member of (orgs)
    "P19":  ("birthplace", 1, True),    # place of birth
    "P452": ("industry", 2, False),     # industry
    # ── Works boost (films / TV shows / video games) — these properties exist
    #    only on those work entities, so persons and org-brands get nothing here. ──
    "P57":  ("director", 2, False),     # director (film)
    "P161": ("cast", 5, False),         # cast member (film / TV)
    "P170": ("creators", 3, False),     # creator (TV / film)
    "P179": ("series", 1, True),        # part of the series / franchise
    "P449": ("network", 2, False),      # original broadcaster (TV)
    "P178": ("developers", 3, False),   # developer (video game)
    "P123": ("publishers", 3, False),   # publisher (video game / work)
    "P400": ("platforms", 4, False),    # platform (video game)
}

# External-identifier properties that anchor identity on other authoritative
# sources. Each maps to (label, url_template).
_EXTERNAL_ID_PROPS: Dict[str, tuple] = {
    "P345": ("imdb", "https://www.imdb.com/name/{}/"),          # IMDb ID
    "P1902": ("spotify", "https://open.spotify.com/artist/{}"),  # Spotify artist
    "P4985": ("tmdb", "https://www.themoviedb.org/person/{}"),   # TMDb person
}

# Cache metadata per resolved key so repeated rows for the same person are cheap.
_METADATA_CACHE: Dict[str, "WikiMetadata"] = {}


@dataclass
class WikiMetadata:
    """Compact structured identity record extracted from Wikipedia/Wikidata."""

    talent: str
    wikipedia_url: str = ""
    qid: str = ""
    name: str = ""
    aliases: List[str] = field(default_factory=list)
    professions: List[str] = field(default_factory=list)
    nationalities: List[str] = field(default_factory=list)
    birth_year: str = ""
    known_works: List[str] = field(default_factory=list)
    official_website: str = ""
    summary: str = ""
    # Wikidata-declared social profiles ({platform: url}) — most trusted source.
    social_links: Dict[str, str] = field(default_factory=dict)
    # Reference identity sources beyond Wikipedia ({source: url}), e.g. IMDb,
    # Spotify, TMDb — used by the verifier as cross-reference signals.
    reference_urls: Dict[str, str] = field(default_factory=dict)
    # Extended structured attributes (gender, teams, sport, position, education,
    # awards, genres, employers, birthplace, industry, birth_date, age,
    # nicknames, active_years, career_keywords, …).
    attributes: Dict[str, Any] = field(default_factory=dict)
    # Extra identity columns provided in the input spreadsheet. Especially
    # important when no Wikipedia URL / Wikidata entity is available.
    input_metadata: Dict[str, str] = field(default_factory=dict)
    # Entity type. Defaults to person so name-only / unknown inputs keep the
    # exact current (talent) behaviour; set False for brands / networks / orgs.
    is_person: bool = True
    entity_type: str = ""
    found: bool = False

    def to_prompt_dict(self) -> dict:
        """Full structured ground-truth profile handed to the LLM (never the page)."""
        data: Dict[str, Any] = {
            "name": self.name or self.talent,
            "aliases": self.aliases,
            "professions": self.professions,
            "nationalities": self.nationalities,
            "birth_year": self.birth_year,
            "known_works": self.known_works,
            "official_website": self.official_website,
            "summary": self.summary,
        }
        # Merge extended attributes (only non-empty ones).
        for key, value in self.attributes.items():
            if value not in ("", None, [], {}):
                data[key] = value
        if self.reference_urls:
            # Authoritative non-Wikipedia sources (IMDb, Spotify, TMDb, …).
            data["reference_sources"] = self.reference_urls
        if self.social_links:
            # Wikidata-declared official handles — cross-reference across platforms.
            data["known_official_profiles"] = self.social_links
        if self.input_metadata:
            # Identity details supplied directly in the spreadsheet.
            data["provided_metadata"] = self.input_metadata
        # Tell the LLM whether this is a person or an organization/brand.
        data["entity_type"] = self.entity_type or ("person" if self.is_person else "organization")
        # Drop empty top-level fields to keep the payload tight.
        return {k: v for k, v in data.items() if v not in ("", None, [], {})}


# ────────────────────────────────────────────────────────────────────────────
#  Label resolution (QID → human-readable English label)
# ────────────────────────────────────────────────────────────────────────────

def _resolve_labels(qids: List[str]) -> Dict[str, str]:
    """Batch-resolve Wikidata QIDs to English labels via wbgetentities."""
    qids = [q for q in dict.fromkeys(qids) if q]
    if not qids:
        return {}
    labels: Dict[str, str] = {}
    # wbgetentities accepts up to 50 ids per call.
    for start in range(0, len(qids), 50):
        chunk = qids[start:start + 50]
        resp = wd._get(
            "https://www.wikidata.org/w/api.php",
            params={
                "action": "wbgetentities",
                "ids": "|".join(chunk),
                "props": "labels",
                "languages": "en",
                "format": "json",
            },
        )
        if not resp:
            continue
        try:
            entities = resp.json().get("entities", {})
            for qid, entity in entities.items():
                label = (entity.get("labels", {}).get("en", {}) or {}).get("value", "")
                if label:
                    labels[qid] = label.strip()
        except Exception as exc:  # noqa: BLE001 — best-effort enrichment
            print(f"  [WIKI-META] Label resolve failed: {exc}")
    return labels


def _claim_qids(claims: dict, prop: str, limit: int = 6) -> List[str]:
    """Return the referenced QIDs for a Wikidata property (entity-valued claims)."""
    out: List[str] = []
    for snak in claims.get(prop, [])[:limit]:
        try:
            value = snak["mainsnak"]["datavalue"]["value"]
            qid = value.get("id") if isinstance(value, dict) else None
            if qid:
                out.append(qid)
        except (KeyError, IndexError, TypeError):
            continue
    return out


def _claim_string(claims: dict, prop: str) -> str:
    """Return the first string value for a Wikidata property (e.g. external ids)."""
    for snak in claims.get(prop, [])[:1]:
        try:
            value = snak["mainsnak"]["datavalue"]["value"]
            if isinstance(value, str) and value.strip():
                return value.strip()
        except (KeyError, IndexError, TypeError):
            continue
    return ""


def _extract_reference_urls(claims: dict) -> Dict[str, str]:
    """Build authoritative reference URLs (IMDb, Spotify, TMDb, …) from external ids."""
    refs: Dict[str, str] = {}
    for prop, (label, template) in _EXTERNAL_ID_PROPS.items():
        value = _claim_string(claims, prop)
        if value:
            refs[label] = template.format(value)
    return refs


def _claim_monolingual(claims: dict, prop: str, limit: int = 4) -> List[str]:
    """Return monolingual-text values for a property (e.g. nicknames)."""
    out: List[str] = []
    for snak in claims.get(prop, [])[:limit]:
        try:
            value = snak["mainsnak"]["datavalue"]["value"]
            text = value.get("text") if isinstance(value, dict) else value
            if text and str(text).strip():
                out.append(str(text).strip())
        except (KeyError, IndexError, TypeError):
            continue
    return out


def _time_year(snak: dict) -> Optional[int]:
    try:
        time_str = snak["mainsnak"]["datavalue"]["value"]["time"]
        m = re.search(r"\+(\d{1,4})-", time_str)
        return int(m.group(1)) if m else None
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _claim_full_date(claims: dict, prop: str) -> str:
    """Return an ISO-ish date 'YYYY-MM-DD' (or 'YYYY') for a time-valued property."""
    for snak in claims.get(prop, [])[:1]:
        try:
            time_str = snak["mainsnak"]["datavalue"]["value"]["time"]
            m = re.search(r"\+(\d{4})-(\d{2})-(\d{2})", time_str)
            if m:
                y, mo, d = m.group(1), m.group(2), m.group(3)
                return f"{y}-{mo}-{d}" if mo != "00" else y
        except (KeyError, IndexError, TypeError):
            continue
    return ""


def _active_years(claims: dict) -> str:
    """Compose an 'active years' string from work-period start/end (P2031/P2032)."""
    start = end = None
    for snak in claims.get(_PROP_WORK_START, [])[:1]:
        start = _time_year(snak)
    for snak in claims.get(_PROP_WORK_END, [])[:1]:
        end = _time_year(snak)
    if start and end:
        return f"{start}–{end}"
    if start:
        return f"{start}–present"
    return ""


def _extract_extended_attributes(claims: dict) -> tuple:
    """
    Return (attributes, entity_qids) where entity_qids is the flat list of all
    QIDs referenced by the extended entity-valued properties (for one batched
    label resolve by the caller).
    """
    entity_field_qids: Dict[str, List[str]] = {}
    all_qids: List[str] = []
    for prop, (fname, limit, _single) in _ENTITY_PROPS.items():
        qids = _claim_qids(claims, prop, limit=limit)
        if qids:
            entity_field_qids[fname] = qids
            all_qids.extend(qids)

    attributes: Dict[str, Any] = {}
    # String / date attributes (no label resolution needed).
    birth_date = _claim_full_date(claims, _PROP_BIRTH_DATE)
    if birth_date:
        attributes["birth_date"] = birth_date
        m = re.match(r"(\d{4})", birth_date)
        if m:
            age = datetime.now().year - int(m.group(1))
            if 0 < age < 130:
                attributes["age"] = age
    nicknames = _claim_monolingual(claims, _PROP_NICKNAME)
    if nicknames:
        attributes["nicknames"] = nicknames
    active = _active_years(claims)
    if active:
        attributes["active_years"] = active

    # Release/premiere year — for works (films, games, TV series). These date
    # properties don't appear on person entities, so persons are unaffected.
    for prop in (_PROP_PUB_DATE, _PROP_START_TIME):
        year = None
        for snak in claims.get(prop, [])[:1]:
            year = _time_year(snak)
        if year:
            attributes["release_year"] = year
            break

    return attributes, entity_field_qids, all_qids


def _claim_birth_year(claims: dict) -> str:
    """Extract the birth year from P569 (time value like '+1990-05-02T00:00:00Z')."""
    for snak in claims.get(_PROP_BIRTH_DATE, [])[:1]:
        try:
            time_str = snak["mainsnak"]["datavalue"]["value"]["time"]
            m = re.search(r"([+-])(\d{1,4})-", time_str)
            if m:
                year = int(m.group(2))
                return str(year) if m.group(1) == "+" else f"-{year}"
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return ""


def _entity_aliases(entity: dict) -> List[str]:
    """English aliases + also-known-as labels declared on the Wikidata entity."""
    aliases: List[str] = []
    for alias in entity.get("aliases", {}).get("en", []) or []:
        value = (alias.get("value") or "").strip()
        if value:
            aliases.append(value)
    return aliases


# ────────────────────────────────────────────────────────────────────────────
#  Wikipedia REST summary (short description + extract only)
# ────────────────────────────────────────────────────────────────────────────

def _fetch_rest_summary(wikipedia_url: str) -> Dict[str, str]:
    """Fetch the compact REST summary (description + extract). No full page."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(wikipedia_url)
        if "/wiki/" not in (parsed.path or ""):
            return {}
        page_title = parsed.path.split("/wiki/", 1)[1].strip("/")
        summary_url = (
            f"{parsed.scheme}://{parsed.netloc}/api/rest_v1/page/summary/{page_title}"
        )
        resp = wd._get(summary_url, timeout=10)
        if not resp:
            return {}
        payload = resp.json()
        return {
            "title": str(payload.get("title") or "").strip(),
            "description": str(payload.get("description") or "").strip(),
            "extract": str(payload.get("extract") or "").strip(),
        }
    except Exception as exc:  # noqa: BLE001
        print(f"  [WIKI-META] REST summary failed: {exc}")
        return {}


# ────────────────────────────────────────────────────────────────────────────
#  Public entry point
# ────────────────────────────────────────────────────────────────────────────

def _meta_get(input_metadata: Dict[str, str], *keys: str) -> str:
    """Case-insensitive lookup across the spreadsheet-provided metadata."""
    lowered = {str(k).lower(): v for k, v in (input_metadata or {}).items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value and str(value).strip():
            return str(value).strip()
    return ""


def fetch_wiki_metadata(
    talent: str,
    wikipedia_url: str = "",
    input_metadata: Optional[Dict[str, str]] = None,
    title_category: str = "",
    title_sub_category: str = "",
) -> WikiMetadata:
    """
    Build a structured :class:`WikiMetadata` record for one talent.

    ``input_metadata`` carries any extra identity columns from the input
    spreadsheet. It is always attached to the record (so the verifier can use
    it) and, when no Wikipedia URL is provided, it also seeds the disambiguation
    hints for the name-based Wikidata search.

    Gracefully degrades: if the Wikipedia URL is missing/invalid or the entity
    cannot be resolved, returns a record with ``found=False`` populated with
    whatever is available (at minimum the talent name + provided metadata).
    """
    talent = (talent or "").strip()
    wikipedia_url = (wikipedia_url or "").strip()
    input_metadata = input_metadata or {}

    # Fall back to spreadsheet columns for the search disambiguation hints.
    title_category = title_category or _meta_get(input_metadata, "title_category", "category")
    title_sub_category = title_sub_category or _meta_get(
        input_metadata, "title_sub_category", "sub_category", "subcategory"
    )

    meta_fingerprint = "|".join(f"{k}={v}" for k, v in sorted(input_metadata.items()))
    cache_key = wikipedia_url or f"{talent.lower()}::{meta_fingerprint}"
    if cache_key in _METADATA_CACHE:
        return _METADATA_CACHE[cache_key]

    meta = WikiMetadata(
        talent=talent, wikipedia_url=wikipedia_url, name=talent,
        input_metadata=input_metadata,
    )

    # 1) Resolve the Wikidata QID (from URL if present, else best-effort search).
    qid: Optional[str] = None
    try:
        if wikipedia_url:
            qid = wd._wikipedia_url_to_qid(wikipedia_url)
        if not qid:
            qid = wd._name_to_qid(talent, title_category, title_sub_category)
    except Exception as exc:  # noqa: BLE001
        print(f"  [WIKI-META] QID resolution error for '{talent}': {exc}")

    if not qid:
        print(f"  [WIKI-META] No Wikidata entity for '{talent}' — using name only.")
        # Still attempt a REST summary if we have a URL (helps disambiguation).
        if wikipedia_url:
            summary = _fetch_rest_summary(wikipedia_url)
            meta.summary = _compose_summary(summary)
            if summary.get("title"):
                meta.name = summary["title"]
        _METADATA_CACHE[cache_key] = meta
        return meta

    meta.qid = qid

    # 2) Fetch the full Wikidata entity (structured claims + labels + aliases).
    entity = None
    try:
        entity = wd._fetch_wikidata_entity(qid)
    except Exception as exc:  # noqa: BLE001
        print(f"  [WIKI-META] Entity fetch error ({qid}): {exc}")

    if entity:
        claims = entity.get("claims", {})

        label = (entity.get("labels", {}).get("en", {}) or {}).get("value", "")
        if label:
            meta.name = label.strip()

        meta.aliases = _entity_aliases(entity)
        meta.birth_year = _claim_birth_year(claims)

        # Extended attributes + all entity QIDs to resolve in a single batch.
        attributes, entity_field_qids, extended_qids = _extract_extended_attributes(claims)

        # Resolve entity-valued properties to labels in one batched call.
        occupation_qids = _claim_qids(claims, _PROP_OCCUPATION)
        citizenship_qids = _claim_qids(claims, _PROP_CITIZENSHIP)
        work_qids = _claim_qids(claims, _PROP_NOTABLE_WORK)
        instance_of_qids = _claim_qids(claims, _PROP_INSTANCE_OF, limit=4)
        labels = _resolve_labels(
            occupation_qids + citizenship_qids + work_qids + extended_qids + instance_of_qids
        )

        # Entity type: person (Q5) vs organization/brand/network/franchise.
        meta.is_person = _QID_HUMAN in instance_of_qids
        meta.entity_type = next((labels[q] for q in instance_of_qids if q in labels), "")

        meta.professions = [labels[q] for q in occupation_qids if q in labels]
        meta.nationalities = [labels[q] for q in citizenship_qids if q in labels]
        meta.known_works = [labels[q] for q in work_qids if q in labels]

        # Map resolved labels back into the extended attributes.
        for prop, (fname, _limit, single) in _ENTITY_PROPS.items():
            qids = entity_field_qids.get(fname, [])
            vals = [labels[q] for q in qids if q in labels]
            if vals:
                attributes[fname] = vals[0] if single else vals

        # Derived career keywords (helps the LLM match profession/domain).
        keyword_sources = (
            meta.professions
            + list(attributes.get("sports", []) if isinstance(attributes.get("sports"), list) else [])
            + list(attributes.get("genres", []) if isinstance(attributes.get("genres"), list) else [])
            + list(attributes.get("position", []) if isinstance(attributes.get("position"), list) else [])
        )
        career_keywords: List[str] = []
        seen_kw: set = set()
        for kw in keyword_sources:
            k = kw.lower()
            if k not in seen_kw:
                seen_kw.add(k)
                career_keywords.append(kw)
        if career_keywords:
            attributes["career_keywords"] = career_keywords

        meta.attributes = attributes

        # Official website + Wikidata-declared social profiles (trusted candidates).
        try:
            socials = wd._extract_wikidata_socials(entity)
        except Exception:  # noqa: BLE001
            socials = {}
        if "_website" in socials:
            meta.official_website = socials["_website"][0]
        for platform, (url, _handle, _prop) in socials.items():
            if not platform.startswith("_"):
                meta.social_links[platform] = url

        # Reference identity sources beyond Wikipedia (IMDb, Spotify, TMDb).
        meta.reference_urls = _extract_reference_urls(claims)

        meta.found = True

    # 3) Compact REST summary for concise natural-language context.
    if wikipedia_url:
        summary = _fetch_rest_summary(wikipedia_url)
        meta.summary = _compose_summary(summary)
        if not meta.name and summary.get("title"):
            meta.name = summary["title"]

    print(
        f"  [WIKI-META] '{talent}' -> qid={meta.qid or '-'} | "
        f"prof={meta.professions or '-'} | nat={meta.nationalities or '-'} | "
        f"born={meta.birth_year or '-'} | site={meta.official_website or '-'} | "
        f"wikidata_socials={list(meta.social_links.keys()) or 'none'}"
    )

    _METADATA_CACHE[cache_key] = meta
    return meta


def _compose_summary(summary: Dict[str, str]) -> str:
    """Join REST description + a bounded extract into one short context string."""
    parts: List[str] = []
    if summary.get("description"):
        parts.append(summary["description"])
    if summary.get("extract"):
        parts.append(summary["extract"][:400])
    return " — ".join(parts)[:600]


def clear_cache() -> None:
    """Clear the per-run metadata cache (called at the start of each pipeline run)."""
    _METADATA_CACHE.clear()
