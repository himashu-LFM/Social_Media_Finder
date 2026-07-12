"""
verification_pipeline.py  —  Talent social-profile verification orchestrator
=============================================================================

Ties the services together into the workflow:

    Excel row (Talent Name + Wikipedia URL)
      -> Wikipedia structured metadata            (wikipedia_service)
      -> Apify social-link discovery              (apify_service)
      -> Serper fallback for missing platforms    (serper_service)
      -> LLM verification per platform            (verification_service)
      -> status + confidence written to the row   (excel_service schema)

Exposes the same interface the FastAPI layer already consumes, so
``api_server.py`` only needs to change which module it imports:

    PLATFORMS
    SERPER_API_KEY
    load_talent_table_from_path(path) -> DataFrame
    run_pipeline_for_names(names, progress, platform_progress) -> DataFrame
    run_pipeline_on_dataframe(df, progress, platform_progress) -> DataFrame
    save_output(df, output_dir) -> str

Design notes:
  • Rows are processed sequentially (so the per-row progress UI stays exact),
    but the independent per-platform verifications within a row run
    concurrently.
  • One Apify call and one Wikipedia lookup per talent; Serper only fires for
    platforms still missing; the LLM only runs when candidates exist. No
    duplicate work per talent/platform.
  • A failure on one talent (or one platform) is logged and isolated — the
    run always continues.
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
    STATUS_LIKELY,
    STATUS_NOT_FOUND,
    STATUS_REJECTED,
    STATUS_REVIEW,
    STATUS_VERIFIED,
    VerificationResult,
)

# Re-exported for api_server compatibility.
PLATFORMS: Dict[str, List[str]] = social_urls.PLATFORMS
SERPER_API_KEY = serper_service.SERPER_API_KEY

# A verification is "solidly good" (no Serper backtrack needed) only for these.
# Anything else — Needs Manual Review, Rejected, or an LLM decline — triggers a
# Serper fallback + re-verification.
_GOOD_STATUSES = {STATUS_VERIFIED, STATUS_LIKELY}

# Ordering used to keep the better of two verification results.
_STATUS_RANK = {
    STATUS_NOT_FOUND: 0,
    STATUS_REJECTED: 1,
    STATUS_REVIEW: 2,
    STATUS_LIKELY: 3,
    STATUS_VERIFIED: 4,
}

# Max concurrent per-platform verifications within a single talent row.
_MAX_PLATFORM_WORKERS = 5

# Delegated so callers can use the pipeline as a single import.
load_talent_table_from_path = excel_service.load_talent_table_from_path
save_output = excel_service.save_output


# ────────────────────────────────────────────────────────────────────────────
#  Candidate gathering
# ────────────────────────────────────────────────────────────────────────────

def _add_candidate(candidates: List[dict], seen: set, platform: str, url: str,
                   source: str, meta: Optional[dict] = None,
                   title: str = "", snippet: str = "") -> None:
    """Append a validated, de-duplicated candidate to the working list."""
    if not url or social_urls.platform_from_url(url) != platform:
        return
    if not social_urls.is_valid_profile_url(url, platform):
        return
    norm = social_urls.normalize_profile_url(url, platform)
    if norm in seen:
        return
    seen.add(norm)
    candidates.append({
        "url": norm, "source": source,
        "meta": meta or {}, "title": title, "snippet": snippet,
    })


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
    talent: str, platform: str, candidates: List[dict], seen: set,
) -> None:
    """Append Serper's top-4 profile candidates to the working list (in place)."""
    try:
        for cand in serper_service.search_platform_candidates(talent, platform, top_n=4):
            _add_candidate(candidates, seen, platform, cand.get("url", ""), "serper",
                           None, cand.get("title", ""), cand.get("snippet", ""))
    except RuntimeError as exc:
        # Fatal Serper error (quota/auth): skip, keep whatever we have.
        print(f"  [PIPELINE] Serper unavailable for {platform}/{talent}: {exc}")


def _verify(platform: str, wiki_meta: wikipedia_service.WikiMetadata,
            candidates: List[dict]) -> VerificationResult:
    if not candidates:
        return VerificationResult(platform=platform, status=STATUS_NOT_FOUND,
                                  reason="No candidate links to verify.")
    # Fetch each candidate's public profile metadata so the LLM reasons over
    # evidence (username, display name, bio, verified, website, followers), not
    # just the URL. Idempotent + cached, so re-verification does not refetch.
    profile_metadata.enrich_candidates(candidates, platform)
    return verification_service.verify_platform(platform, wiki_meta.to_prompt_dict(), candidates)


def _score(result: VerificationResult) -> tuple:
    return (_STATUS_RANK.get(result.status, 0), result.confidence)


def _finalize(result: VerificationResult, candidates: List[dict]) -> VerificationResult:
    """
    Decide the link/status/confidence actually written to the row.

    Every platform with at least one candidate shows a link — the LLM's pick when
    it made one, otherwise the top candidate flagged for manual review. Only a
    platform with zero candidates anywhere ends up blank (Not Found).
    """
    if result.best_candidate:
        return result
    if candidates:
        return VerificationResult(
            platform=result.platform,
            best_candidate=candidates[0]["url"],
            status=STATUS_REVIEW,
            confidence=result.confidence,
            reason=(result.reason or "") + " | LLM did not confirm; top candidate shown for review.",
            evidence=result.evidence,
            rejected=result.rejected,
        )
    return VerificationResult(platform=result.platform, status=STATUS_NOT_FOUND,
                              reason=result.reason or "No candidates found.")


def _resolve_platform(
    talent: str,
    platform: str,
    wiki_meta: wikipedia_service.WikiMetadata,
    apify_candidates: Dict[str, List[dict]],
) -> VerificationResult:
    """
    Two-phase resolution for one platform:
      Phase 1 — verify Wikidata/Apify candidates (skipped if there are none).
      Phase 2 — if that is not solidly good, pull Serper's top-4 and re-verify
                the combined set; keep whichever result scores higher.
    Always returns a finalized result (link populated whenever any candidate exists).
    """
    candidates, seen = _apify_candidates(platform, wiki_meta, apify_candidates)
    result = _verify(platform, wiki_meta, candidates) if candidates else None

    needs_backtrack = result is None or result.status not in _GOOD_STATUSES
    if needs_backtrack:
        before = len(candidates)
        _add_serper_candidates(talent, platform, candidates, seen)
        if len(candidates) > before or result is None:
            reason = "no Apify candidate" if before == 0 else f"'{result.status}' from Apify"
            print(f"  [BACKTRACK] {platform} | {talent} -> Serper ({reason})")
            serper_result = _verify(platform, wiki_meta, candidates)
            if result is None or _score(serper_result) >= _score(result):
                result = serper_result

    if result is None:
        result = VerificationResult(platform=platform, status=STATUS_NOT_FOUND,
                                    reason="No candidate links found.")
    return _finalize(result, candidates)


# ────────────────────────────────────────────────────────────────────────────
#  Row / dataframe processing
# ────────────────────────────────────────────────────────────────────────────

def _write_result(df: pd.DataFrame, row_label: object, result: VerificationResult) -> None:
    platform = result.platform
    has_link = bool(result.best_candidate)
    df.at[row_label, excel_service.link_col(platform)] = result.best_candidate
    df.at[row_label, excel_service.status_col(platform)] = result.status
    df.at[row_label, excel_service.conf_col(platform)] = result.confidence if has_link else ""


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

    # Step 1-2: structured Wikipedia metadata (no full page to the LLM).
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

    # Step 3: one Apify lookup per talent (all platforms at once).
    try:
        apify_candidates = apify_service.find_social_links(talent, wiki_meta.official_website)
    except Exception as exc:  # noqa: BLE001
        print(f"  [PIPELINE] Apify lookup failed for '{talent}': {exc}")
        apify_candidates = {}

    # Steps 4-5: per-platform candidate gathering + LLM verification (concurrent).
    def _task(platform: str) -> VerificationResult:
        if platform_progress:
            platform_progress(platform, "start")
        try:
            result = _resolve_platform(talent, platform, wiki_meta, apify_candidates)
        except Exception as exc:  # noqa: BLE001 — isolate platform failures
            print(f"  [PIPELINE] {platform} verification error for '{talent}': {exc}")
            result = VerificationResult(
                platform=platform, status=STATUS_REVIEW, confidence=0,
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

    # Step 6-7: write results + overall confidence (main thread, no races).
    found_confidences: List[int] = []
    for result in results:
        _write_result(df, row_label, result)
        if result.best_candidate:
            found_confidences.append(result.confidence)

    df.at[row_label, excel_service.OVERALL_CONF_COL] = (
        round(sum(found_confidences) / len(found_confidences)) if found_confidences else ""
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
    # Ensure schema columns are object dtype (mixed str/int confidence cells).
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
