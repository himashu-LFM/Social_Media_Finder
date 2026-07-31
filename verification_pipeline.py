"""
verification_pipeline.py  —  Talent social-profile verification orchestrator
=============================================================================

Workflow per talent (Serper-primary, Apify-backup):

    Excel row (Talent Name + optional Wikipedia URL + any extra metadata)
      1. Wikipedia/Wikidata -> rich ground-truth profile   (wikipedia_service)
      2. Serper (PRIMARY)   -> "<name> site:<domain>" search, TOP profile /
                               YouTube-channel result per platform, with snippet
      3. LLM                -> Verified / Wrong / Manual Review / Not Found
                               with confidence + reason        (verification_service)
      4. Apify (BACKUP)     -> only for platforms that came back Wrong / Not Found
                               / Manual Review. For Manual Review, if Apify returns
                               the SAME url as Serper -> Verified (cross-source
                               agreement); otherwise the Apify link is Serper-
                               searched for context and re-verified by the LLM.
                               The better of the two results is kept.
      5. results written to the row                            (excel_service schema)

Exposes the interface the FastAPI layer consumes:
    PLATFORMS, SERPER_API_KEY,
    load_talent_table_from_path, run_pipeline_for_names,
    run_pipeline_on_dataframe, save_output

Design notes:
  • Serper is the PRIMARY link source (one "site:" search per platform); Apify is
    the BACKUP, batched across ONLY the talents that had a non-Verified platform,
    so it scales to hundreds of rows without a per-row actor run.
  • Two global phases keep Apify batched: Phase A verifies every row's Serper
    links concurrently; Phase B runs one batched Apify pass for the failing
    talents, then re-verifies just their failing platforms.
  • Per-talent and per-platform failures are isolated — the run always continues.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

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

# A Verified platform is final; anything else (Wrong / Manual / Not Found) is
# eligible for the Apify backup pass.
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

# Platforms where the direct OG-tag page fetch actually works. IG/TikTok/X block
# it, so we skip it there (Serper context is the evidence source for those).
_OG_FETCH_PLATFORMS = {"Facebook", "YouTube"}

# Thin-evidence platforms (no OG fetch): fetch extra Serper context for the exact
# profile URL — even for Serper-found links — so the LLM gets the profile's own
# description + follower counts instead of just the site: search snippet.
_CONTEXT_FETCH_PLATFORMS = {"Instagram", "TikTok", "X"}

# Max concurrent per-platform verifications within a single talent row.
_MAX_PLATFORM_WORKERS = 5

# Max talent rows processed concurrently. Tune to your API plan (higher = faster
# but more requests/min → risk of rate limits). Env-overridable.
PIPELINE_ROW_WORKERS = max(1, int(os.environ.get("PIPELINE_ROW_WORKERS", "4")))

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


def _serper_primary_candidate(talent: str, platform: str) -> tuple[List[dict], bool]:
    """
    PRIMARY discovery: a Serper "<name> site:<platform-domain>" search, returning
    the TOP valid profile (or YouTube channel) result only — with its snippet.

    Returns ``(candidates, errored)``: ``errored`` is True when Serper itself
    failed (quota/auth/connection) so the caller can distinguish a genuine
    "no profile" from a failed search.
    """
    try:
        return serper_service.discover_by_site(talent, platform, top_n=1), False
    except RuntimeError as exc:
        print(f"  [PIPELINE] Serper site-search unavailable for {platform}/{talent}: {exc}")
        return [], True


def _apify_only_candidates(
    platform: str, apify_candidates: Dict[str, List[dict]],
) -> List[dict]:
    """BACKUP source: Apify-discovered candidates for one platform (validated)."""
    candidates: List[dict] = []
    seen: set = set()
    for cand in apify_candidates.get(platform, []):
        _add_candidate(candidates, seen, platform, cand.get("url", ""),
                       cand.get("source", "apify"), cand.get("meta"))
    return candidates


def _enrich_candidates(candidates: List[dict], platform: str) -> None:
    """
    Attach evidence to each candidate before verification:
      • Serper context (search the link -> title/snippet) for links that did NOT
        already come from a Serper search (i.e. Apify links)
      • fetched public profile metadata (OG tags) as a supplement (FB/YouTube)
    Both merge into candidate['meta']; already-enriched candidates are skipped.
    """
    for cand in candidates:
        if cand.get("_enriched"):
            continue
        # Fetch Serper context for the exact profile URL when the link did NOT come
        # from a Serper search (Apify links), OR for thin-evidence platforms
        # (IG/TikTok/X) even for Serper links — the URL search returns the profile's
        # own description + follower counts, far richer than the site: snippet alone.
        needs_context = (
            cand.get("source") != "serper" or platform in _CONTEXT_FETCH_PLATFORMS
        )
        if needs_context:
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
    # Supplement with OG-tag profile metadata ONLY where it works — Facebook and
    # YouTube expose OG tags (verified badge / bio / followers). Instagram, TikTok
    # and X block it and return nothing, so we skip the wasted fetch there.
    if platform in _OG_FETCH_PLATFORMS:
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


# ────────────────────────────────────────────────────────────────────────────
#  Per-platform resolution: Serper primary (Phase 1) + Apify backup (Phase 2)
# ────────────────────────────────────────────────────────────────────────────

def _resolve_platform_serper(
    talent: str, platform: str, wiki_meta: wikipedia_service.WikiMetadata,
) -> VerificationResult:
    """Phase 1 — verify the Serper top-result candidate (primary discovery)."""
    candidates, errored = _serper_primary_candidate(talent, platform)
    if not candidates:
        reason = (
            "Serper search errored (connection/rate limit) — result unconfirmed, "
            "not a definite absence; Apify backup will be tried."
            if errored else "No profile found via Serper search."
        )
        return VerificationResult(platform=platform, status=STATUS_NOT_FOUND, reason=reason)
    return _verify(platform, wiki_meta, candidates)


def _apify_backup_platform(
    talent: str,
    platform: str,
    wiki_meta: wikipedia_service.WikiMetadata,
    phase1: VerificationResult,
    apify_candidates: Dict[str, List[dict]],
) -> VerificationResult:
    """
    Phase 2 (BACKUP) — runs only when Serper's result was NOT Verified.

    Verifies the Apify candidate(s) via the same sub-pipeline (Serper-search the
    link for its snippet -> hand it to the LLM against the ground truth) and keeps
    whichever of {Serper result, Apify result} scores higher. Every verdict is the
    LLM's — no result is hardcoded (a candidate is never auto-Verified just because
    Serper and Apify happened to return the same link; the LLM still decides).
    """
    apify_cands = _apify_only_candidates(platform, apify_candidates)
    if not apify_cands:
        return phase1  # no Apify link to fall back on — keep Serper's result

    # Cross-source agreement is EVIDENCE, not a verdict: if Apify independently
    # returned the SAME link Serper already surfaced, flag it so the LLM can weigh
    # the agreement (it raises confidence the link is genuine) — but the LLM still
    # decides whether it's actually this person against the ground truth.
    serper_url = phase1.best_candidate
    if serper_url:
        for cand in apify_cands:
            if cand["url"] == serper_url:
                cand["meta"]["found_by_serper_and_apify"] = True
                print(f"  [AGREE] {platform} | {talent} -> Serper & Apify returned the same link")

    print(f"  [BACKUP] {platform} | {talent} -> Apify ('{phase1.status}' from Serper)")
    apify_result = _verify(platform, wiki_meta, apify_cands)
    # Keep the better of the two LLM verdicts.
    return apify_result if _score(apify_result) >= _score(phase1) else phase1


# ────────────────────────────────────────────────────────────────────────────
#  Per-row phases
# ────────────────────────────────────────────────────────────────────────────

def _row_serper_phase(
    talent: str,
    wiki_meta: wikipedia_service.WikiMetadata,
    platform_progress: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, VerificationResult]:
    """Phase 1 for one row: Serper-primary verify across all platforms (concurrent)."""
    def _task(platform: str) -> tuple:
        if platform_progress:
            platform_progress(platform, "start")
        try:
            result = _resolve_platform_serper(talent, platform, wiki_meta)
        except Exception as exc:  # noqa: BLE001 — isolate platform failures
            print(f"  [PIPELINE] {platform} Serper phase error for '{talent}': {exc}")
            result = VerificationResult(
                platform=platform, status=STATUS_MANUAL, confidence=0,
                reason="Verification error; manual review required.",
            )
        finally:
            if platform_progress:
                platform_progress(platform, "done")
        return platform, result

    results: Dict[str, VerificationResult] = {}
    workers = min(_MAX_PLATFORM_WORKERS, len(PLATFORMS))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for platform, result in executor.map(_task, list(PLATFORMS)):
            results[platform] = result
    return results


def _row_apify_phase(
    talent: str,
    wiki_meta: wikipedia_service.WikiMetadata,
    phase1: Dict[str, VerificationResult],
    apify_candidates: Dict[str, List[dict]],
) -> Dict[str, VerificationResult]:
    """Phase 2 for one row: Apify backup for the platforms Serper didn't Verify."""
    final = dict(phase1)
    failing = [p for p, r in phase1.items() if r.status not in _GOOD_STATUSES]
    if not failing:
        return final

    def _task(platform: str) -> tuple:
        try:
            result = _apify_backup_platform(
                talent, platform, wiki_meta, phase1[platform], apify_candidates or {}
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  [PIPELINE] {platform} Apify backup error for '{talent}': {exc}")
            result = phase1[platform]
        return platform, result

    workers = min(_MAX_PLATFORM_WORKERS, len(failing))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        for platform, result in executor.map(_task, failing):
            final[platform] = result
    return final


def _handle_from_url(url: str) -> str:
    """Best-effort username/handle from a normalized profile URL, lowercased."""
    if not url:
        return ""
    try:
        path = urlparse(url).path.strip("/")
    except Exception:  # noqa: BLE001
        return ""
    if not path:
        return ""
    # Strip YouTube path prefixes so channel/user/c handles compare cleanly.
    for prefix in ("channel/", "user/", "c/"):
        if path.lower().startswith(prefix):
            path = path[len(prefix):]
            break
    return path.split("/")[0].lstrip("@").lower()


def _corroborate_row(
    talent: str,
    wiki_meta: wikipedia_service.WikiMetadata,
    results: Dict[str, VerificationResult],
) -> Dict[str, VerificationResult]:
    """
    Cross-platform corroboration. Handles CONFIRMED (Verified) on strong platforms
    this run are passed to the LLM as an extra evidence field when RE-checking the
    platforms that only reached Manual Review — a handle already confirmed
    elsewhere is strong support. The LLM still decides; we keep the better verdict.
    Only runs when there's at least one Verified handle AND a Manual-Review platform.
    """
    verified_handles: Dict[str, str] = {}
    for platform, result in results.items():
        if result.status == STATUS_VERIFIED and result.best_candidate:
            handle = _handle_from_url(result.best_candidate)
            if handle:
                verified_handles[platform] = handle

    rescue = [p for p, r in results.items() if r.status == STATUS_MANUAL and r.best_candidate]
    if not verified_handles or not rescue:
        return results

    # Ground truth + the cross-platform confirmed handles as an extra hint field.
    gt = dict(wiki_meta.to_prompt_dict())
    gt["verified_handles_on_other_platforms"] = verified_handles

    final = dict(results)

    def _task(platform: str) -> tuple:
        cand = {"url": results[platform].best_candidate, "source": "apify", "meta": {}}
        try:
            _enrich_candidates([cand], platform)  # re-fetch snippet/kg/counts for the link
            res = verification_service.verify_platform(
                platform, gt, [cand], is_person=wiki_meta.is_person
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  [CORROBORATE] {platform} | {talent} error: {exc.__class__.__name__}")
            return platform, results[platform]
        if _score(res) >= _score(results[platform]):
            print(f"  [CORROBORATE] {platform} | {talent}: {results[platform].status} -> {res.status}")
            return platform, res
        return platform, results[platform]

    with ThreadPoolExecutor(max_workers=min(_MAX_PLATFORM_WORKERS, len(rescue))) as executor:
        for platform, result in executor.map(_task, rescue):
            final[platform] = result
    return final


def _assemble_row_out(results: Dict[str, VerificationResult]) -> Dict[str, Any]:
    """Turn per-platform results into the row's {column -> value} dict."""
    out: Dict[str, Any] = {}
    usable_confidences: List[int] = []
    for platform in PLATFORMS:
        result = results.get(platform) or VerificationResult(
            platform=platform, status=STATUS_NOT_FOUND
        )
        has_link = bool(result.best_candidate)
        out[excel_service.link_col(platform)] = result.best_candidate
        out[excel_service.status_col(platform)] = result.status
        out[excel_service.conf_col(platform)] = result.confidence if has_link else ""
        out[excel_service.reason_col(platform)] = result.reason
        if has_link and result.status in _USABLE_STATUSES:
            usable_confidences.append(result.confidence)
    out[excel_service.OVERALL_CONF_COL] = (
        round(sum(usable_confidences) / len(usable_confidences)) if usable_confidences else ""
    )
    return out


def _ground_truth(talent: str, wiki_url: str, input_metadata: dict) -> wikipedia_service.WikiMetadata:
    """Step 1 — rich Wikipedia/Wikidata ground-truth profile (unchanged)."""
    try:
        return wikipedia_service.fetch_wiki_metadata(
            talent, wiki_url, input_metadata=input_metadata
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  [PIPELINE] Wikipedia metadata failed for '{talent}': {exc}")
        return wikipedia_service.WikiMetadata(
            talent=talent, wikipedia_url=wiki_url, name=talent,
            input_metadata=input_metadata,
        )


# ────────────────────────────────────────────────────────────────────────────
#  Row / dataframe processing
# ────────────────────────────────────────────────────────────────────────────

def _resolve_row_result(
    talent: str,
    wiki_url: str,
    input_metadata: dict,
    apify_candidates: Optional[Dict[str, List[dict]]] = None,
    platform_progress: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, Any]:
    """
    Run the full verification workflow for ONE talent (both phases) and return a
    dict of {column -> value}. Pure compute (no DataFrame writes).

    ``apify_candidates`` is the pre-fetched Apify slice for this talent; if None,
    a per-talent Apify lookup is done ONLY when Serper leaves a platform
    non-Verified (used by the single-row / CLI path).
    """
    wiki_meta = _ground_truth(talent, wiki_url, input_metadata)

    # Phase 1 — Serper-primary discovery + verification.
    phase1 = _row_serper_phase(talent, wiki_meta, platform_progress)

    # Phase 2 — Apify backup, only if some platform isn't Verified.
    failing = [p for p, r in phase1.items() if r.status not in _GOOD_STATUSES]
    if failing:
        if apify_candidates is None:
            try:
                apify_candidates = apify_service.find_social_links(talent, wiki_meta.official_website)
            except Exception as exc:  # noqa: BLE001
                print(f"  [PIPELINE] Apify lookup failed for '{talent}': {exc}")
                apify_candidates = {}
        final = _row_apify_phase(talent, wiki_meta, phase1, apify_candidates)
    else:
        final = phase1

    # Cross-platform corroboration: rescue Manual Reviews using handles confirmed
    # (Verified) on other platforms this run.
    final = _corroborate_row(talent, wiki_meta, final)
    return _assemble_row_out(final)


def _row_inputs(df: pd.DataFrame, row_label: object) -> tuple:
    """Read (talent, wiki_url, input_metadata) for a row."""
    talent = str(df.at[row_label, excel_service.TALENT_COL] or "").strip()
    wiki_url = str(df.at[row_label, excel_service.WIKI_COL] or "").strip()
    input_metadata: dict = {}
    if excel_service.INPUT_META_COL in df.columns:
        raw_meta = df.at[row_label, excel_service.INPUT_META_COL]
        if isinstance(raw_meta, dict):
            input_metadata = raw_meta
    return talent, wiki_url, input_metadata


def process_row(
    df: pd.DataFrame,
    row_label: object,
    platform_progress: Optional[Callable[[str, str], None]] = None,
) -> None:
    """Single-row entry point (in place). Kept for the sequential / CLI path."""
    talent, wiki_url, input_metadata = _row_inputs(df, row_label)
    if not talent:
        return
    print(f"\n{'=' * 65}\nProcessing: {talent}")
    result = _resolve_row_result(talent, wiki_url, input_metadata,
                                 apify_candidates=None, platform_progress=platform_progress)
    for col, val in result.items():
        df.at[row_label, col] = val


def run_pipeline_on_dataframe(
    df: pd.DataFrame,
    row_status: Optional[Callable[[int, str], None]] = None,
    platform_progress: Optional[Callable[[int, str, str], None]] = None,
) -> pd.DataFrame:
    """
    Process every row and return the populated frame — Serper primary, Apify backup.

    Two global phases keep the run scalable to hundreds of rows:
      Phase A — every row's Serper-primary discovery + verification, concurrently
                (up to PIPELINE_ROW_WORKERS rows at once; platforms concurrent).
      Phase B — ONE batched Apify pass for only the talents that had a
                non-Verified platform, then re-verify just those platforms.

    Workers only compute; the DataFrame is written on the main thread as Phase B
    futures complete, so there are no concurrent pandas writes.
    ``row_status(row_index, status)`` reports per-row lifecycle ("processing"/"done").
    """
    df = df.copy()
    for col in excel_service.ordered_columns():
        if col not in df.columns:
            df[col] = ""
    df = df.astype({col: object for col in excel_service.ordered_columns() if col in df.columns})

    wikipedia_service.clear_cache()
    profile_metadata.clear_cache()
    serper_service.clear_cache()

    # Collect processable rows (skip blank names), preserving 0-based index.
    rows = []
    for idx, row_label in enumerate(df.index):
        talent, wiki_url, input_metadata = _row_inputs(df, row_label)
        if talent:
            rows.append((idx, row_label, talent, wiki_url, input_metadata))

    total = len(rows)
    print(f"[PIPELINE] Verification run started for {total} talent row(s) "
          f"(Serper primary, Apify backup; row workers={PIPELINE_ROW_WORKERS}).")

    # ── Phase A: Serper-primary discovery + verification for every row ──
    def _phase_a(entry: tuple) -> dict:
        idx, row_label, talent, wiki_url, input_metadata = entry
        if row_status:
            row_status(idx, "processing")

        def _pp(platform: str, phase: str) -> None:
            if platform_progress:
                platform_progress(idx, platform, phase)

        wiki_meta = _ground_truth(talent, wiki_url, input_metadata)
        try:
            phase1 = _row_serper_phase(talent, wiki_meta, _pp)
        except Exception as exc:  # noqa: BLE001 — one bad row must not stop the run
            print(f"  [PIPELINE] Serper phase failed for '{talent}': {exc}")
            phase1 = {
                p: VerificationResult(platform=p, status=STATUS_MANUAL, confidence=0,
                                      reason="Verification error; manual review required.")
                for p in PLATFORMS
            }
        return {"idx": idx, "row_label": row_label, "talent": talent,
                "wiki_meta": wiki_meta, "phase1": phase1}

    workers = max(1, min(PIPELINE_ROW_WORKERS, total or 1))
    phase_a: List[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for res in pool.map(_phase_a, rows):
            phase_a.append(res)

    # ── One batched Apify pass for ONLY the talents with a non-Verified platform ──
    failing_talents = sorted({
        r["talent"] for r in phase_a
        if any(v.status not in _GOOD_STATUSES for v in r["phase1"].values())
    })
    print(f"[PIPELINE] Phase B (Apify backup): {len(failing_talents)} of {total} "
          f"talent(s) have a non-Verified platform.")
    try:
        apify_map = apify_service.find_social_links_batch(failing_talents) if failing_talents else {}
    except Exception as exc:  # noqa: BLE001
        print(f"  [PIPELINE] Batched Apify backup failed: {exc}")
        apify_map = {}

    # ── Phase B: Apify backup + assemble + write ──
    def _phase_b(r: dict) -> tuple:
        try:
            final = _row_apify_phase(
                r["talent"], r["wiki_meta"], r["phase1"], apify_map.get(r["talent"], {})
            )
            final = _corroborate_row(r["talent"], r["wiki_meta"], final)
            out = _assemble_row_out(final)
        except Exception as exc:  # noqa: BLE001
            print(f"  [PIPELINE] Apify phase failed for '{r['talent']}': {exc}")
            out = _assemble_row_out(r["phase1"])
        return r["row_label"], r["idx"], out

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_phase_b, r) for r in phase_a]
        for future in as_completed(futures):
            row_label, idx, out = future.result()
            for col, val in out.items():  # main-thread write — no races
                df.at[row_label, col] = val
            if row_status:
                row_status(idx, "done")
            done += 1
            print(f"  [{done}/{total}] complete")

    # Companion "Serper-only" view: the Phase A (Serper + LLM) results BEFORE any
    # Apify backup or cross-platform corroboration, so the UI can show what Serper
    # alone produced. Stashed on the frame so the caller can save it separately.
    serper_df = df.copy()
    for r in phase_a:
        for col, val in _assemble_row_out(r["phase1"]).items():
            serper_df.at[r["row_label"], col] = val
    df.attrs["serper_df"] = serper_df

    return df


def run_pipeline_for_names(
    names: List[str],
    row_status: Optional[Callable[[int, str], None]] = None,
    platform_progress: Optional[Callable[[int, str, str], None]] = None,
) -> pd.DataFrame:
    """Names-only entry point (no Wikipedia URLs — metadata falls back to search)."""
    clean = [str(name).strip() for name in names if name and str(name).strip()]
    if not clean:
        raise ValueError("At least one non-empty name is required.")
    df = excel_service.build_talent_df(clean)
    return run_pipeline_on_dataframe(df, row_status=row_status, platform_progress=platform_progress)
