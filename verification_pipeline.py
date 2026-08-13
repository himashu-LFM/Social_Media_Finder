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
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

import pandas as pd

import apify_service
import excel_service
import profile_metadata
import serper_service
import serpapi_service
import social_urls
import verification_service
import wikipedia_service
from verification_service import (
    STATUS_MANUAL,
    STATUS_NOT_FOUND,
    STATUS_STOPPED,
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

# Platforms where the direct OG-tag page fetch actually works. TikTok and X
# return nothing, so they are skipped. Instagram is included because although it
# withholds the bio, its og:description carries follower/following/post counts
# and og:title carries the display name — which is exactly what separates a real
# account from a fan page or an empty impostor with the same name.
_OG_FETCH_PLATFORMS = {"Facebook", "YouTube", "Instagram"}

# Candidates fetched per platform from Serper. Was 1, which made namesake
# collisions invisible: the model could not weigh three same-named accounts
# because it only ever saw one of them. Env-overridable.
SERPER_CANDIDATES_PER_PLATFORM = max(
    1, int(os.environ.get("SERPER_CANDIDATES_PER_PLATFORM", "4"))
)

# When several live accounts all claim the same identity, prefer surfacing the
# choice to an analyst over silently picking one. Set to "0" to disable.
AMBIGUITY_GUARD = os.environ.get("AMBIGUITY_GUARD", "1").strip() not in ("0", "false", "no")

# Max concurrent per-platform verifications within a single talent row.
_MAX_PLATFORM_WORKERS = 5

# Max talent rows processed concurrently. Tune to your API plan (higher = faster
# but more requests/min → risk of rate limits). Env-overridable.
PIPELINE_ROW_WORKERS = max(1, int(os.environ.get("PIPELINE_ROW_WORKERS", "4")))

# Delegated so callers can use the pipeline as a single import.
load_talent_table_from_path = excel_service.load_talent_table_from_path
save_output = excel_service.save_output

# Reason written to any platform/row that was skipped because the operator
# stopped the run. Kept inside the existing 4 statuses (Not Found) so the export
# schema and the UI legend stay unchanged.
CANCELLED_REASON = "Run stopped by user before this platform was searched."


def _is_cancelled(should_cancel: Optional[Callable[[], bool]]) -> bool:
    """True when the caller has asked the run to stop. Never raises."""
    if should_cancel is None:
        return False
    try:
        return bool(should_cancel())
    except Exception:  # noqa: BLE001 — a broken callback must not abort the run
        return False


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
        return serper_service.discover_by_site(
            talent, platform, top_n=SERPER_CANDIDATES_PER_PLATFORM
        ), False
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
        # Serper context for links that didn't come from a Serper search (Apify).
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
    # Supplement with OG-tag profile metadata ONLY where it works — Facebook and
    # YouTube expose OG tags (verified badge / bio / followers). Instagram, TikTok
    # and X block it and return nothing, so we skip the wasted fetch there.
    if platform in _OG_FETCH_PLATFORMS:
        profile_metadata.enrich_candidates(candidates, platform)


def _drop_missing_profiles(candidates: List[dict], platform: str) -> List[dict]:
    """
    Remove candidates the platform itself reports as non-existent (hard 404).

    A guessed handle can otherwise pick up plausible-looking Serper context —
    Google returns pages about the person for a URL that was never created — and
    get Verified. Only YouTube and X give a trustworthy signal, so only they are
    probed; everything else passes through untouched.
    """
    kept: List[dict] = []
    for cand in candidates:
        url = cand.get("url", "")
        if profile_metadata.profile_is_missing(url, platform):
            print(f"  [DEAD] {platform} candidate does not exist (404) — dropped: {url}")
            continue
        kept.append(cand)
    return kept


def _verify(platform: str, wiki_meta: wikipedia_service.WikiMetadata,
            candidates: List[dict], allow_thin_verify: bool = False) -> VerificationResult:
    if not candidates:
        return VerificationResult(platform=platform, status=STATUS_NOT_FOUND,
                                  reason="No candidate links to verify.")
    candidates = _drop_missing_profiles(candidates, platform)
    if not candidates:
        return VerificationResult(
            platform=platform, status=STATUS_NOT_FOUND,
            reason="Candidate profile(s) returned HTTP 404 — the handle does not exist.",
        )
    _enrich_candidates(candidates, platform)
    result = verification_service.verify_platform(
        platform, wiki_meta.to_prompt_dict(), candidates, is_person=wiki_meta.is_person,
        allow_thin_verify=allow_thin_verify,
    )
    return _guard_ambiguous_identity(
        result, candidates, wiki_meta.name or wiki_meta.talent, platform
    )


def _score(result: VerificationResult) -> tuple:
    return (_STATUS_RANK.get(result.status, 0), result.confidence)


def _name_tokens(text: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 1}


def _claims_identity(cand: dict, talent: str) -> bool:
    """
    True when a candidate's own displayed identity carries the full talent name.

    Reads the display name / page title, NOT the handle — a handle can be any
    string, but an account presenting itself as "Toby Kebbell" is competing for
    the same identity regardless of how its URL is spelled.
    """
    meta = cand.get("meta") or {}
    shown = " ".join(str(meta.get(k, "")) for k in
                     ("display_name", "serper_title", "title", "knowledge_graph"))
    wanted = _name_tokens(talent)
    return bool(wanted) and wanted.issubset(_name_tokens(shown))


def _describe(cand: dict) -> str:
    """One-line candidate summary for the analyst-facing Reason column."""
    meta = cand.get("meta") or {}
    bits = [str(meta[k]) for k in ("followers", "subscribers") if meta.get(k)]
    if meta.get("posts"):
        bits.append(f"{meta['posts']} posts")
    stats = f" ({', '.join(bits)})" if bits else ""
    return f"{cand.get('url', '')}{stats}"


def _guard_ambiguous_identity(
    result: VerificationResult, candidates: List[dict], talent: str, platform: str,
) -> VerificationResult:
    """
    Refuse to silently pick between several live accounts of the same name.

    Namesakes, fan pages and impostors are the dominant precision failure here:
    "Toby Kebbell" has three live Instagram accounts all displaying that exact
    name. Picking one and stamping it Verified hides a judgement an analyst
    should make, so when two or more candidates claim the identity we downgrade
    to Manual Review and list every contender WITH its follower counts — which
    is usually enough for a human to decide in seconds.
    """
    if not AMBIGUITY_GUARD or result.status != STATUS_VERIFIED or not result.best_candidate:
        return result
    rivals = [c for c in candidates
              if c.get("url") != result.best_candidate and _claims_identity(c, talent)]
    if not rivals:
        return result
    chosen = next((c for c in candidates if c.get("url") == result.best_candidate), None)
    options = " | ".join(_describe(c) for c in ([chosen] if chosen else []) + rivals)
    print(f"  [AMBIGUOUS] {platform} | {talent}: {len(rivals) + 1} accounts claim this "
          f"identity — routed to manual review")
    return VerificationResult(
        platform=platform,
        best_candidate=result.best_candidate,
        status=STATUS_MANUAL,
        confidence=min(result.confidence, 75),
        reason=(f"{len(rivals) + 1} live accounts present themselves as '{talent}' on "
                f"{platform}; picking one automatically risks a fan page or namesake. "
                f"Candidates: {options}. Model's preference: {result.best_candidate}. "
                + (result.reason or "")).strip(),
        evidence=result.evidence,
        rejected=result.rejected,
        decision=result.decision,
    )


# ────────────────────────────────────────────────────────────────────────────
#  Per-platform resolution: Serper primary (Phase 1) + Apify backup (Phase 2)
# ────────────────────────────────────────────────────────────────────────────

# Minimum handle length before we will reuse it on another platform. Short
# slugs ("nba", "abc") collide with unrelated accounts far too often.
_MIN_FANOUT_SLUG_LEN = 5


def _fanout_slugs(input_handles: Dict[str, str]) -> List[str]:
    """
    Distinct handle slugs taken from the client's own recorded profiles.

    Actors and brands overwhelmingly reuse one handle across platforms, so a
    handle the client already has on file for Instagram is a strong direct
    candidate for X / TikTok / YouTube — no search required.
    """
    slugs: List[str] = []
    for platform, url in (input_handles or {}).items():
        slug = social_urls.handle_from_url(url, platform)
        if len(slug) >= _MIN_FANOUT_SLUG_LEN and slug.lower() not in [s.lower() for s in slugs]:
            slugs.append(slug)
    return slugs


def _analyst_result(platform: str, url: str) -> VerificationResult:
    """A decision a human already made. Outranks anything the pipeline can infer."""
    return VerificationResult(
        platform=platform, best_candidate=url, status=STATUS_VERIFIED, confidence=100,
        reason="Confirmed by an analyst in a previous run (verified_url).",
        decision="verified",
    )


def _resolve_platform_serper(
    talent: str,
    platform: str,
    wiki_meta: wikipedia_service.WikiMetadata,
    known_url: str = "",
    fanout_slugs: Optional[List[str]] = None,
    decisions: Optional[Dict[str, Dict[str, str]]] = None,
) -> VerificationResult:
    """
    Phase 1 — assemble candidates for one platform and verify them together.

    Candidate order (all are judged by the LLM; none is auto-accepted):
      1. the profile the CLIENT already has on file for this platform
      2. the top Serper "<name> site:<domain>" result
      3. ONLY if 1 and 2 produced nothing: the client's handle from ANOTHER
         platform, reused here — a cheap recall rescue that costs no search.
    """
    decisions = decisions or {}
    # A human already ruled on this cell — return it and spend nothing. This is
    # the whole point of persisting decisions: one analyst's work makes every
    # later run of the same talent cheaper AND more accurate.
    confirmed = (decisions.get("verified") or {}).get(platform)
    if confirmed:
        print(f"  [DECISION] {platform} | {talent} -> served from verified_url (no API spend)")
        return _analyst_result(platform, confirmed)

    # A human rejected these — never surface them again, on any run.
    rejected = {u for u in [(decisions.get("rejected") or {}).get(platform)] if u}

    serper_candidates, errored = _serper_primary_candidate(talent, platform)

    candidates: List[dict] = []
    seen: set = set()
    if known_url:
        _add_candidate(candidates, seen, platform, known_url, "input",
                       {"supplied_in_client_record": True})
    for cand in serper_candidates:
        _add_candidate(candidates, seen, platform, cand.get("url", ""),
                       cand.get("source", "serper"), cand.get("meta"))

    if not candidates:
        for slug in (fanout_slugs or []):
            url = social_urls.profile_url_from_handle(slug, platform)
            _add_candidate(candidates, seen, platform, url, "handle_fanout",
                           {"handle_reused_from_client_profile_on_another_platform": slug})
        if candidates:
            print(f"  [FANOUT] {platform} | {talent} -> trying known handle(s) "
                  f"{fanout_slugs} (Serper found nothing)")

    # Drop anything an analyst has already rejected, comparing on the normalised
    # URL so a trailing slash or scheme difference can't smuggle it back in.
    if rejected:
        norm_rejected = {social_urls.normalize_profile_url(u, platform).lower() for u in rejected}
        before = len(candidates)
        candidates = [c for c in candidates
                      if social_urls.normalize_profile_url(c["url"], platform).lower()
                      not in norm_rejected]
        if len(candidates) < before:
            print(f"  [DECISION] {platform} | {talent} -> dropped "
                  f"{before - len(candidates)} previously rejected candidate(s)")

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
    should_cancel: Optional[Callable[[], bool]] = None,
    input_handles: Optional[Dict[str, str]] = None,
    decisions: Optional[Dict[str, Dict[str, str]]] = None,
) -> Dict[str, VerificationResult]:
    """Phase 1 for one row: Serper-primary verify across all platforms (concurrent)."""
    input_handles = input_handles or {}
    fanout = _fanout_slugs(input_handles)

    def _task(platform: str) -> tuple:
        # Queued platform tasks drain as cheap no-ops once a stop is requested;
        # already-running searches finish so we never abandon a paid API call.
        if _is_cancelled(should_cancel):
            return platform, VerificationResult(
                platform=platform, status=STATUS_STOPPED, reason=CANCELLED_REASON
            )
        if platform_progress:
            platform_progress(platform, "start")
        try:
            result = _resolve_platform_serper(
                talent, platform, wiki_meta,
                known_url=input_handles.get(platform, ""), fanout_slugs=fanout,
                decisions=decisions,
            )
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
        # Same 404 guard as _verify — cross-platform corroboration must never
        # promote a handle that doesn't resolve.
        if not _drop_missing_profiles([cand], platform):
            return platform, VerificationResult(
                platform=platform, status=STATUS_NOT_FOUND,
                reason="Candidate profile returned HTTP 404 — the handle does not exist.",
            )
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


def _ground_truth(talent: str, wiki_url: str, input_metadata: dict,
                  input_handles: Optional[Dict[str, str]] = None
                  ) -> wikipedia_service.WikiMetadata:
    """Step 1 — rich Wikipedia/Wikidata ground-truth profile."""
    try:
        meta = wikipedia_service.fetch_wiki_metadata(
            talent, wiki_url, input_metadata=input_metadata
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  [PIPELINE] Wikipedia metadata failed for '{talent}': {exc}")
        meta = wikipedia_service.WikiMetadata(
            talent=talent, wikipedia_url=wiki_url, name=talent,
            input_metadata=input_metadata,
        )
    # Attach the client's own recorded profiles. Set on the metadata object so
    # EVERY consumer sees it — Serper phase, Apify backup and corroboration —
    # without threading an extra argument through each call site. The cache in
    # wikipedia_service is keyed on name/URL, so copy before mutating.
    if input_handles:
        meta = replace(meta, client_recorded_profiles=dict(input_handles))
    return meta


# ────────────────────────────────────────────────────────────────────────────
#  No-Wikipedia flow — SerpApi "Google AI Mode" discovery + LLM tagging
# ────────────────────────────────────────────────────────────────────────────

# Excel columns (case-insensitive) that carry a talent's profession/category,
# used to build the query "<name> <profession> all social media handles".
# An explicit ``profession``/``occupation`` column wins when present (it is the
# cleanest, most direct signal); otherwise fall back to the taxonomy columns.
_PROFESSION_KEYS = (
    "profession", "occupation",
    "title_sub_category", "title_subcategory", "sub_category", "subcategory",
    "title_category", "category",
    "primary_genre", "genre", "role",
)


def _clean_profession(value: str) -> str:
    """
    Strip a 'Label - Value' prefix so queries stay clean.
    e.g. 'Talent Type - Musician' -> 'Musician', 'Actor' -> 'Actor'.
    """
    parts = re.split(r"\s+[-–—]\s+|:\s+", value.strip())
    return (parts[-1].strip() if parts else value.strip())


def _detect_profession(input_metadata: dict) -> str:
    """Best-effort profession/category term from the row's Excel columns."""
    if not input_metadata:
        return ""
    lowered = {str(k).lower(): v for k, v in input_metadata.items()}
    for key in _PROFESSION_KEYS:
        val = lowered.get(key)
        if isinstance(val, str) and val.strip():
            return _clean_profession(val)
    return ""


def _row_serpapi_phase(
    talent: str,
    wiki_meta: wikipedia_service.WikiMetadata,
    input_metadata: dict,
    platform_progress: Optional[Callable[[str, str], None]] = None,
    custom_query: Optional[str] = None,
    include_profession: bool = True,
) -> Dict[str, VerificationResult]:
    """
    Discovery for talents with NO Wikipedia link: ONE SerpApi Google-AI-Mode
    query, then return the links Google AI Mode cites — tagged Manual Review
    Needed (source: SerpApi).

    Query = ``"<name> [<profession>] <suffix>"``:
      * ``profession`` is taken from the row's Excel columns, and is omitted
        entirely when ``include_profession`` is False.
      * ``custom_query`` overrides the default suffix — this is the operator's
        free-text prompt from the "Without Wikipedia" UI. ``None`` keeps the
        configured default suffix.

    There is NO LLM verification for these rows: the links are handed through
    as-is for a human to review. (No Serper/Apify either — one SerpApi call/row.)
    """
    profession = _detect_profession(input_metadata) if include_profession else ""
    try:
        handles = serpapi_service.discover_handles(talent, profession, suffix=custom_query)
    except Exception as exc:  # noqa: BLE001
        print(f"  [PIPELINE] SerpApi discovery failed for '{talent}': {exc}")
        handles = {}

    results: Dict[str, VerificationResult] = {}
    for platform in PLATFORMS:
        if platform_progress:
            platform_progress(platform, "start")
        cands = handles.get(platform, [])
        if cands:
            results[platform] = VerificationResult(
                platform=platform, best_candidate=cands[0]["url"],
                status=STATUS_MANUAL, confidence=0, decision="manual_review",
                reason=("Link cited by SerpApi Google AI Mode search — not "
                        "LLM-verified; manual review needed."),
            )
        else:
            results[platform] = VerificationResult(
                platform=platform, status=STATUS_NOT_FOUND,
                reason="No link returned by Google AI Mode search.",
            )
        if platform_progress:
            platform_progress(platform, "done")
    return results


# ────────────────────────────────────────────────────────────────────────────
#  Row / dataframe processing
# ────────────────────────────────────────────────────────────────────────────

def _resolve_row_result(
    talent: str,
    wiki_url: str,
    input_metadata: dict,
    apify_candidates: Optional[Dict[str, List[dict]]] = None,
    platform_progress: Optional[Callable[[str, str], None]] = None,
    input_handles: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Run the full verification workflow for ONE talent (both phases) and return a
    dict of {column -> value}. Pure compute (no DataFrame writes).

    ``apify_candidates`` is the pre-fetched Apify slice for this talent; if None,
    a per-talent Apify lookup is done ONLY when Serper leaves a platform
    non-Verified (used by the single-row / CLI path).
    """
    wiki_meta = _ground_truth(talent, wiki_url, input_metadata, input_handles)

    # No Wikipedia link -> SerpApi Google AI Mode discovery + LLM tagging
    # (a different search API and pattern; no Serper/Apify for these rows).
    if not (wiki_url or "").strip():
        final = _row_serpapi_phase(talent, wiki_meta, input_metadata, platform_progress)
        return _assemble_row_out(final)

    # Phase 1 — Serper-primary discovery + verification.
    phase1 = _row_serper_phase(talent, wiki_meta, platform_progress,
                               input_handles=input_handles,
                               decisions=load_decisions([talent]).get(talent.lower(), {}))

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


def load_decisions(talents: List[str]) -> Dict[str, Dict[str, Dict[str, str]]]:
    """
    Analyst decisions for these talents, keyed by lowercased title.

    Read once per run. Returns {} when the database is unconfigured or
    unreachable — a missing decision store must never stop a run, it only means
    the run costs full price.
    """
    try:
        import db_service
        if not db_service.is_configured():
            return {}
        found = db_service.fetch_decisions(talents)
        if found:
            v = sum(len(s.get("verified", {})) for s in found.values())
            r = sum(len(s.get("rejected", {})) for s in found.values())
            print(f"[DECISIONS] {v} confirmed and {r} rejected cell(s) loaded for "
                  f"{len(found)} talent(s) — confirmed cells cost nothing to re-run.")
        return found
    except Exception as exc:  # noqa: BLE001 — never block a run on the decision store
        print(f"[DECISIONS] unavailable ({exc.__class__.__name__}) — running at full cost.")
        return {}


def _row_inputs(df: pd.DataFrame, row_label: object) -> tuple:
    """Read (talent, wiki_url, input_metadata, input_handles) for a row."""
    talent = str(df.at[row_label, excel_service.TALENT_COL] or "").strip()
    wiki_url = str(df.at[row_label, excel_service.WIKI_COL] or "").strip()
    input_metadata: dict = {}
    if excel_service.INPUT_META_COL in df.columns:
        raw_meta = df.at[row_label, excel_service.INPUT_META_COL]
        if isinstance(raw_meta, dict):
            input_metadata = raw_meta
    input_handles: dict = {}
    if excel_service.INPUT_HANDLES_COL in df.columns:
        raw_handles = df.at[row_label, excel_service.INPUT_HANDLES_COL]
        if isinstance(raw_handles, dict):
            input_handles = raw_handles
    return talent, wiki_url, input_metadata, input_handles


def process_row(
    df: pd.DataFrame,
    row_label: object,
    platform_progress: Optional[Callable[[str, str], None]] = None,
) -> None:
    """Single-row entry point (in place). Kept for the sequential / CLI path."""
    talent, wiki_url, input_metadata, input_handles = _row_inputs(df, row_label)
    if not talent:
        return
    print(f"\n{'=' * 65}\nProcessing: {talent}")
    result = _resolve_row_result(talent, wiki_url, input_metadata,
                                 apify_candidates=None, platform_progress=platform_progress,
                                 input_handles=input_handles)
    for col, val in result.items():
        df.at[row_label, col] = val


def run_pipeline_on_dataframe(
    df: pd.DataFrame,
    row_status: Optional[Callable[[int, str], None]] = None,
    platform_progress: Optional[Callable[[int, str, str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    mode: str = "wiki",
    custom_query: str = "",
    include_profession: bool = True,
) -> pd.DataFrame:
    """
    Process every row and return the populated frame — Serper primary, Apify backup.

    ``mode`` selects the discovery strategy for the whole run:
      * ``"wiki"`` (default) — per-row auto routing: rows WITH a Wikipedia URL go
        through Serper + LLM (+ Apify backup); rows without one fall back to the
        SerpApi Google-AI-Mode discovery with the default query suffix.
      * ``"non_wiki"`` — force EVERY row through SerpApi Google-AI-Mode using a
        custom query ``"<name> [<profession>] <custom_query>"``. ``custom_query``
        is the operator's free-text prompt; ``include_profession`` controls
        whether the Excel profession/category is included in the query. No
        Wikipedia lookup, no Serper, no Apify, no LLM for these rows.

    Two global phases keep the run scalable to hundreds of rows:
      Phase A — every row's Serper-primary discovery + verification, concurrently
                (up to PIPELINE_ROW_WORKERS rows at once; platforms concurrent).
      Phase B — ONE batched Apify pass for only the talents that had a
                non-Verified platform, then re-verify just those platforms.

    Workers only compute; the DataFrame is written on the main thread as Phase B
    futures complete, so there are no concurrent pandas writes.
    ``row_status(row_index, status)`` reports per-row lifecycle ("processing"/"done").
    """
    non_wiki_mode = str(mode or "wiki").strip().lower() == "non_wiki"
    if non_wiki_mode:
        print(f"[PIPELINE] Mode: NON-WIKI custom query — suffix={custom_query!r}, "
              f"include_profession={include_profession}. Every row uses SerpApi "
              f"Google AI Mode (no Serper/Apify/LLM).")
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
        talent, wiki_url, input_metadata, input_handles = _row_inputs(df, row_label)
        if talent:
            rows.append((idx, row_label, talent, wiki_url, input_metadata, input_handles))

    total = len(rows)
    print(f"[PIPELINE] Verification run started for {total} talent row(s) "
          f"(Serper primary, Apify backup; row workers={PIPELINE_ROW_WORKERS}).")

    # One lookup for the whole run: cells an analyst already ruled on cost nothing
    # to re-run, and previously rejected URLs are never surfaced again.
    decisions_by_talent = load_decisions([r[2] for r in rows]) if rows else {}

    # ── Phase A: Serper-primary discovery + verification for every row ──
    def _phase_a(entry: tuple) -> dict:
        idx, row_label, talent, wiki_url, input_metadata, input_handles = entry
        # Rows still queued when the operator stops are returned untouched, so no
        # Wikipedia/Serper/LLM budget is spent on work nobody is waiting for.
        if _is_cancelled(should_cancel):
            return {
                "idx": idx, "row_label": row_label, "talent": talent,
                "wiki_meta": wikipedia_service.WikiMetadata(talent=talent, name=talent),
                "phase1": {
                    p: VerificationResult(platform=p, status=STATUS_STOPPED,
                                          reason=CANCELLED_REASON)
                    for p in PLATFORMS
                },
                "cancelled": True,
            }
        if row_status:
            row_status(idx, "processing")

        def _pp(platform: str, phase: str) -> None:
            if platform_progress:
                platform_progress(idx, platform, phase)

        # SerpApi path when the operator chose Non-Wiki mode, OR (in Wiki mode)
        # when a row simply has no Wikipedia URL to anchor on.
        use_serpapi = non_wiki_mode or not (wiki_url or "").strip()
        # No Wikipedia lookup for SerpApi rows — the SerpApi phase never uses the
        # ground-truth record, so skip the (often fruitless) Wikidata/REST calls.
        if use_serpapi:
            wiki_meta = wikipedia_service.WikiMetadata(talent=talent, name=talent)
        else:
            wiki_meta = _ground_truth(talent, wiki_url, input_metadata, input_handles)
        try:
            if use_serpapi:
                # Different pipeline: SerpApi Google AI Mode (no Serper/Apify).
                # The custom query + profession toggle apply only in Non-Wiki mode;
                # Wiki-mode fallback rows keep the default suffix + profession.
                phase1 = _row_serpapi_phase(
                    talent, wiki_meta, input_metadata, _pp,
                    custom_query=(custom_query if non_wiki_mode else None),
                    include_profession=(include_profession if non_wiki_mode else True),
                )
            else:
                phase1 = _row_serper_phase(talent, wiki_meta, _pp, should_cancel,
                                           input_handles=input_handles)
        except Exception as exc:  # noqa: BLE001 — one bad row must not stop the run
            print(f"  [PIPELINE] {'SerpApi' if use_serpapi else 'Serper'} phase failed for '{talent}': {exc}")
            phase1 = {
                p: VerificationResult(platform=p, status=STATUS_MANUAL, confidence=0,
                                      reason="Verification error; manual review required.")
                for p in PLATFORMS
            }
        return {"idx": idx, "row_label": row_label, "talent": talent,
                "wiki_meta": wiki_meta, "phase1": phase1, "cancelled": False,
                "no_wiki": use_serpapi}

    workers = max(1, min(PIPELINE_ROW_WORKERS, total or 1))
    phase_a: List[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for res in pool.map(_phase_a, rows):
            phase_a.append(res)

    # ── One batched Apify pass for ONLY the talents with a non-Verified platform ──
    stopped = _is_cancelled(should_cancel)
    failing_talents = sorted({
        r["talent"] for r in phase_a
        if not r.get("cancelled") and not r.get("no_wiki")  # no-Wiki rows use SerpApi only
        and any(v.status not in _GOOD_STATUSES for v in r["phase1"].values())
    })
    if stopped:
        print("[PIPELINE] Stop requested — skipping the Apify backup pass; "
              "keeping whatever Phase A already verified.")
        apify_map = {}
    else:
        print(f"[PIPELINE] Phase B (Apify backup): {len(failing_talents)} of {total} "
              f"talent(s) have a non-Verified platform.")
        try:
            apify_map = apify_service.find_social_links_batch(failing_talents) if failing_talents else {}
        except Exception as exc:  # noqa: BLE001
            print(f"  [PIPELINE] Batched Apify backup failed: {exc}")
            apify_map = {}

    # ── Phase B: Apify backup + assemble + write ──
    def _phase_b(r: dict) -> tuple:
        # Once stopped, assemble what Phase A produced instead of spending more
        # Apify/LLM calls — partial results are still saved and viewable.
        if r.get("cancelled") or _is_cancelled(should_cancel):
            return r["row_label"], r["idx"], _assemble_row_out(r["phase1"])
        # No-Wikipedia rows are already final from the SerpApi phase (no Apify).
        if r.get("no_wiki"):
            return r["row_label"], r["idx"], _assemble_row_out(r["phase1"])
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
    should_cancel: Optional[Callable[[], bool]] = None,
) -> pd.DataFrame:
    """Names-only entry point (no Wikipedia URLs — metadata falls back to search)."""
    clean = [str(name).strip() for name in names if name and str(name).strip()]
    if not clean:
        raise ValueError("At least one non-empty name is required.")
    df = excel_service.build_talent_df(clean)
    return run_pipeline_on_dataframe(
        df, row_status=row_status, platform_progress=platform_progress,
        should_cancel=should_cancel,
    )
