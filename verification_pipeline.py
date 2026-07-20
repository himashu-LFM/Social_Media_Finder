"""
verification_pipeline.py  —  Talent social-profile verification orchestrator
=============================================================================

Workflow per talent:

    Excel row (Talent Name + Wikipedia URL + any extra metadata)
      1. Wikipedia/Wikidata -> rich ground-truth profile   (wikipedia_service)
      2. Apify              -> candidate links (IG/FB/YT/TikTok)  (apify_service)
      3a. Serper (Part A)   -> context for each Apify link   (serper_service)
      3b. Serper (Part B)   -> backup discovery for missing / not-Verified
      4. LLM                -> Verified / Wrong / Manual Review Needed
                               with confidence + reason        (verification_service)
      5. results written to the row                            (excel_service schema)

Exposes the interface the FastAPI layer consumes:
    PLATFORMS, SERPER_API_KEY,
    load_talent_table_from_path, run_pipeline_for_names,
    run_pipeline_on_dataframe, save_output

Design notes:
  • Rows processed sequentially (exact per-row progress); the five platforms
    within a row are resolved concurrently.
  • Serper is used for BOTH context extraction (every Apify link) and backup
    discovery (missing or not-Verified platforms). Backup discovery + a second
    verification runs whenever the Apify result isn't Verified.
  • Per-talent and per-platform failures are isolated — the run always continues.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional

import pandas as pd

import apify_service
import excel_service
import profile_metadata
import serper_service
import social_urls
import verification_service
import wikipedia_service
from verification_service import (
    STATUS_MANUAL,
    STATUS_NOT_FOUND,
    STATUS_VERIFIED,
    STATUS_WRONG,
    VerificationResult,
)

# Re-exported for api_server compatibility.
PLATFORMS: Dict[str, List[str]] = social_urls.PLATFORMS
SERPER_API_KEY = serper_service.SERPER_API_KEY

# Only a Verified result skips Serper backup discovery; anything else backtracks.
_GOOD_STATUSES = {STATUS_VERIFIED}

# Ordering used to keep the better of two verification results.
_STATUS_RANK = {
    STATUS_NOT_FOUND: 0,
    STATUS_WRONG: 1,
    STATUS_MANUAL: 2,
    STATUS_VERIFIED: 3,
}

# Statuses whose confidence counts toward the overall per-talent score.
_USABLE_STATUSES = {STATUS_VERIFIED, STATUS_MANUAL}

# Max concurrent per-platform verifications within a single talent row.
_MAX_PLATFORM_WORKERS = 5

# Delegated so callers can use the pipeline as a single import.
load_talent_table_from_path = excel_service.load_talent_table_from_path
save_output = excel_service.save_output


# ────────────────────────────────────────────────────────────────────────────
#  Candidate gathering
# ────────────────────────────────────────────────────────────────────────────

def _add_candidate(candidates: List[dict], seen: set, platform: str, url: str,
                   source: str, meta: Optional[dict] = None) -> None:
    """Append a validated, de-duplicated candidate to the working list."""
    if not url or social_urls.platform_from_url(url) != platform:
        return
    if not social_urls.is_valid_profile_url(url, platform):
        return
    norm = social_urls.normalize_profile_url(url, platform)
    if norm in seen:
        return
    seen.add(norm)
    candidates.append({"url": norm, "source": source, "meta": dict(meta or {})})


def _apify_candidates(
    platform: str,
    wiki_meta: wikipedia_service.WikiMetadata,
    apify_candidates: Dict[str, List[dict]],
) -> tuple[List[dict], set]:
    """Wikidata-declared + Apify-discovered candidates (most trusted first)."""
    candidates: List[dict] = []
    seen: set = set()
    wikidata_url = wiki_meta.social_links.get(platform, "")
    if wikidata_url:
        _add_candidate(candidates, seen, platform, wikidata_url, "wikidata",
                       {"declared_on_wikidata": True})
    for cand in apify_candidates.get(platform, []):
        _add_candidate(candidates, seen, platform, cand.get("url", ""),
                       cand.get("source", "apify"), cand.get("meta"))
    return candidates, seen


def _add_serper_candidates(
    talent: str, platform: str, identifiers: str, candidates: List[dict], seen: set,
) -> None:
    """Part B — append Serper backup-discovery candidates (in place)."""
    try:
        for cand in serper_service.discover_candidates(talent, platform, identifiers, top_n=3):
            _add_candidate(candidates, seen, platform, cand.get("url", ""),
                           "serper", cand.get("meta"))
    except RuntimeError as exc:
        # Fatal Serper error (quota/auth): skip, keep whatever we have.
        print(f"  [PIPELINE] Serper unavailable for {platform}/{talent}: {exc}")


def _enrich_candidates(candidates: List[dict], platform: str) -> None:
    """
    Attach evidence to each candidate before verification:
      • Serper context (Part A) for links that didn't come from a Serper search
      • fetched public profile metadata (OG tags) as a supplement
    Both merge into candidate['meta']; already-enriched candidates are skipped.
    """
    for cand in candidates:
        if cand.get("_enriched"):
            continue
        # Part A: Serper context for existing (Apify/Wikidata) links.
        if cand.get("source") != "serper":
            try:
                ctx = serper_service.context_for_url(cand.get("url", ""))
            except RuntimeError as exc:
                print(f"  [PIPELINE] Serper context unavailable: {exc}")
                ctx = {}
            if ctx:
                merged = dict(ctx)
                merged.update({k: v for k, v in cand["meta"].items() if v not in ("", None)})
                cand["meta"] = merged
        cand["_enriched"] = True
    # Supplement with OG-tag profile metadata (fills verified badge / bio on FB/YT).
    profile_metadata.enrich_candidates(candidates, platform)


def _verify(platform: str, wiki_meta: wikipedia_service.WikiMetadata,
            candidates: List[dict]) -> VerificationResult:
    if not candidates:
        return VerificationResult(platform=platform, status=STATUS_NOT_FOUND,
                                  reason="No candidate links to verify.")
    _enrich_candidates(candidates, platform)
    return verification_service.verify_platform(
        platform, wiki_meta.to_prompt_dict(), candidates, is_person=wiki_meta.is_person
    )


def _score(result: VerificationResult) -> tuple:
    return (_STATUS_RANK.get(result.status, 0), result.confidence)


def _resolve_platform(
    talent: str,
    platform: str,
    identifiers: str,
    wiki_meta: wikipedia_service.WikiMetadata,
    apify_candidates: Dict[str, List[dict]],
) -> VerificationResult:
    """
    Resolve one platform:
      Phase 1 — verify Wikidata/Apify candidates (with Serper context) if any.
      Phase 2 — if not Verified (or no Apify link), run Serper backup discovery
                and re-verify the combined set; keep whichever scores higher.
    """
    candidates, seen = _apify_candidates(platform, wiki_meta, apify_candidates)
    result = _verify(platform, wiki_meta, candidates) if candidates else None

    if result is None or result.status not in _GOOD_STATUSES:
        before = len(candidates)
        _add_serper_candidates(talent, platform, identifiers, candidates, seen)
        if len(candidates) > before:
            reason = "no Apify candidate" if before == 0 else f"'{result.status}' from Apify"
            print(f"  [BACKTRACK] {platform} | {talent} -> Serper ({reason})")
            serper_result = _verify(platform, wiki_meta, candidates)
            if result is None or _score(serper_result) >= _score(result):
                result = serper_result

    if result is None:
        result = VerificationResult(platform=platform, status=STATUS_NOT_FOUND,
                                    reason="No candidate links found.")
    return result


def _build_identifiers(wiki_meta: wikipedia_service.WikiMetadata) -> str:
    """Short distinguishing-facts string for Serper backup-discovery queries."""
    attrs = wiki_meta.attributes or {}
    parts: List[str] = []

    def add_attr(*keys: str) -> None:
        for key in keys:
            value = attrs.get(key)
            if isinstance(value, list) and value:
                parts.append(str(value[0]))
            elif isinstance(value, str) and value:
                parts.append(value)

    if wiki_meta.is_person:
        # Person (talent) — unchanged behaviour.
        if wiki_meta.professions:
            parts.append(wiki_meta.professions[0])
        add_attr("sports", "teams", "genres")
        if wiki_meta.nationalities:
            parts.append(wiki_meta.nationalities[0])
    else:
        # Works boost (films / TV shows / video games): these fields are present
        # only for those entities, so brands / networks / franchises / universities
        # fall straight through to the generic org signals below unchanged.
        add_attr("series", "director", "network", "developers", "publishers")
        # Organization / brand / TV network / franchise — generic org facts.
        add_attr("industry", "genres")
        if wiki_meta.known_works:
            parts.append(wiki_meta.known_works[0])
        add_attr("member_of", "employers")
        if wiki_meta.nationalities:
            parts.append(wiki_meta.nationalities[0])

    out: List[str] = []
    seen: set = set()
    for part in parts:
        key = part.lower()
        if part and key not in seen:
            seen.add(key)
            out.append(part)
    return " ".join(out[:4])


# ────────────────────────────────────────────────────────────────────────────
#  Row / dataframe processing
# ────────────────────────────────────────────────────────────────────────────

def _write_result(df: pd.DataFrame, row_label: object, result: VerificationResult) -> None:
    platform = result.platform
    has_link = bool(result.best_candidate)
    df.at[row_label, excel_service.link_col(platform)] = result.best_candidate
    df.at[row_label, excel_service.status_col(platform)] = result.status
    df.at[row_label, excel_service.conf_col(platform)] = result.confidence if has_link else ""
    df.at[row_label, excel_service.reason_col(platform)] = result.reason


def process_row(
    df: pd.DataFrame,
    row_label: object,
    platform_progress: Optional[Callable[[str, str], None]] = None,
) -> None:
    """Run the full verification workflow for one talent row (in place)."""
    talent = str(df.at[row_label, excel_service.TALENT_COL] or "").strip()
    wiki_url = str(df.at[row_label, excel_service.WIKI_COL] or "").strip()
    if not talent:
        return

    # Extra identity columns from the input spreadsheet (used as context,
    # especially when no Wikipedia URL is available for this talent).
    input_metadata: dict = {}
    if excel_service.INPUT_META_COL in df.columns:
        raw_meta = df.at[row_label, excel_service.INPUT_META_COL]
        if isinstance(raw_meta, dict):
            input_metadata = raw_meta

    print(f"\n{'=' * 65}\nProcessing: {talent}")

    # Step 1: rich Wikipedia/Wikidata ground-truth profile (no full page to LLM).
    try:
        wiki_meta = wikipedia_service.fetch_wiki_metadata(
            talent, wiki_url, input_metadata=input_metadata
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  [PIPELINE] Wikipedia metadata failed for '{talent}': {exc}")
        wiki_meta = wikipedia_service.WikiMetadata(
            talent=talent, wikipedia_url=wiki_url, name=talent,
            input_metadata=input_metadata,
        )

    identifiers = _build_identifiers(wiki_meta)

    # Step 2: one Apify lookup per talent (all supported platforms at once).
    try:
        apify_candidates = apify_service.find_social_links(talent, wiki_meta.official_website)
    except Exception as exc:  # noqa: BLE001
        print(f"  [PIPELINE] Apify lookup failed for '{talent}': {exc}")
        apify_candidates = {}

    # Steps 3-4: per-platform context + verification (concurrent across platforms).
    def _task(platform: str) -> VerificationResult:
        if platform_progress:
            platform_progress(platform, "start")
        try:
            result = _resolve_platform(talent, platform, identifiers, wiki_meta, apify_candidates)
        except Exception as exc:  # noqa: BLE001 — isolate platform failures
            print(f"  [PIPELINE] {platform} verification error for '{talent}': {exc}")
            result = VerificationResult(
                platform=platform, status=STATUS_MANUAL, confidence=0,
                reason="Verification error; manual review required.",
            )
        finally:
            if platform_progress:
                platform_progress(platform, "done")
        return result

    results: List[VerificationResult] = []
    workers = min(_MAX_PLATFORM_WORKERS, len(PLATFORMS))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_task, p): p for p in PLATFORMS}
        for future in as_completed(futures):
            results.append(future.result())

    # Step 5: write results + overall confidence (main thread, no races).
    usable_confidences: List[int] = []
    for result in results:
        _write_result(df, row_label, result)
        if result.best_candidate and result.status in _USABLE_STATUSES:
            usable_confidences.append(result.confidence)

    df.at[row_label, excel_service.OVERALL_CONF_COL] = (
        round(sum(usable_confidences) / len(usable_confidences)) if usable_confidences else ""
    )


def run_pipeline_on_dataframe(
    df: pd.DataFrame,
    progress: Optional[Callable[[int, int, str], None]] = None,
    platform_progress: Optional[Callable[[int, str, str], None]] = None,
) -> pd.DataFrame:
    """Process every row of a talent dataframe and return the populated frame."""
    df = df.copy()
    for col in excel_service.ordered_columns():
        if col not in df.columns:
            df[col] = ""
    # Ensure schema columns are object dtype (mixed str/int cells).
    df = df.astype({col: object for col in excel_service.ordered_columns() if col in df.columns})

    wikipedia_service.clear_cache()
    profile_metadata.clear_cache()

    total = len(df)
    print(f"[PIPELINE] Verification run started for {total} talent row(s).")

    for i, row_label in enumerate(df.index, start=1):
        talent = str(df.at[row_label, excel_service.TALENT_COL] or "").strip()
        if not talent:
            continue

        if progress:
            progress(i, total, talent)

        def _row_platform_progress(platform: str, phase: str) -> None:
            if platform_progress:
                platform_progress(i - 1, platform, phase)

        try:
            process_row(df, row_label, platform_progress=_row_platform_progress)
        except Exception as exc:  # noqa: BLE001 — one bad row must not stop the run
            print(f"  [PIPELINE] Row failed for '{talent}': {exc}")

        print(f"  [{i}/{total}] complete")

    return df


def run_pipeline_for_names(
    names: List[str],
    progress: Optional[Callable[[int, int, str], None]] = None,
    platform_progress: Optional[Callable[[int, str, str], None]] = None,
) -> pd.DataFrame:
    """Names-only entry point (no Wikipedia URLs — metadata falls back to search)."""
    clean = [str(name).strip() for name in names if name and str(name).strip()]
    if not clean:
        raise ValueError("At least one non-empty name is required.")
    df = excel_service.build_talent_df(clean)
    return run_pipeline_on_dataframe(df, progress=progress, platform_progress=platform_progress)
