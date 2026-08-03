"""
verification_service.py  —  LLM profile-verification engine
============================================================

Given a talent's rich ground-truth metadata (Wikipedia + Excel), a platform,
and one or more candidate profile URLs — each carrying context extracted from
Serper and any fetched profile metadata — the LLM decides which candidate, if
any, is the official profile.

Rules:
  • The LLM NEVER searches the web. It reasons ONLY over the supplied evidence.
  • ALL candidates for a platform are sent in ONE request; the model ranks them
    and returns the single best (or none).
  • Deep semantic comparison, not string matching: name/aliases, occupation,
    teams, filmography, music, sport, biography, nationality, timeline,
    handle, followers, verification clues, cross-references.
  • Labels: Verified / Wrong / Manual Review Needed (+ Not Found when nothing
    plausible). Minimise false positives — prefer Manual Review over a risky
    Verified.
  • Output is strict JSON:
        {best_candidate, decision, confidence, reason, evidence, rejected}

LLM provider:
  • Anthropic (Claude) is the PRIMARY verification engine.
  • OpenAI is the FALLBACK — used automatically if Anthropic is missing,
    rate-limited, out of credit, or otherwise fails.
Only the LLM call layer changed; the prompt, JSON parsing, labels, confidence
rule, and everything downstream are exactly as before.

Environment variables:
    ANTROPIC_API_KEY   Anthropic key (primary; ANTHROPIC_API_KEY also accepted)
    ANTHROPIC_MODEL    Claude model id (default: claude-opus-4-8)
    OPENAI_API_KEY     OpenAI key (fallback)
    OPENAI_CHAT_MODEL  OpenAI model id (default: gpt-4o-mini)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List

import requests

from retry_util import request_with_retry

# ── Primary LLM: Anthropic (Claude) ──
# Read the user's env spelling (ANTROPIC_API_KEY) first, then the correct one.
ANTHROPIC_API_KEY = (
    os.environ.get("ANTROPIC_API_KEY", "")
    or os.environ.get("ANTHROPIC_API_KEY", "")
).strip()
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8").strip()

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"

# ── Fallback LLM: OpenAI ──
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_CHAT_MODEL = os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"

# Verification labels.
STATUS_VERIFIED = "Verified"
STATUS_WRONG = "Wrong"
STATUS_MANUAL = "Manual Review Needed"
STATUS_NOT_FOUND = "Not Found"
# Never searched, because the operator stopped the run. Deliberately distinct
# from "Not Found": that asserts an absence we actually looked for, and a
# stopped cell asserts nothing at all. Keeping them separate stops a cancelled
# run from being ingested downstream as "this profile does not exist".
STATUS_STOPPED = "Not Checked"

# A candidate may only be labelled Verified at or above this confidence.
_VERIFIED_MIN_CONFIDENCE = 90

# Identity fields that let the model tell the real person/entity apart from a
# namesake. If NONE are present the ground truth is effectively name-only (e.g. a
# Wikipedia 429 wiped the Wikidata lookup, or the entity has no Wikipedia page),
# and a name match alone must NOT be allowed to Verify — it could be any namesake.
_IDENTITY_SIGNALS = (
    "professions", "nationalities", "birth_year", "known_works",
    "summary", "reference_sources",
)


def _ground_truth_is_thin(ground_truth: Dict[str, Any]) -> bool:
    """
    True when the ground truth carries no facts that could rule out a namesake.

    The bar differs by entity type, because the risk differs. Two people called
    "Adam Grissom" is routine, so a person needs real identity facts. Two
    unrelated companies both called "NetBrain" is not routine — a brand name
    plus the client's recorded industry is already enough to disambiguate, so
    organizations are not treated as thin when the input file describes them.
    Persons are unchanged: this only ever loosens the gate for non-persons.
    """
    if any(ground_truth.get(k) for k in _IDENTITY_SIGNALS):
        return False
    entity_type = str(ground_truth.get("entity_type", "")).strip().lower()
    is_org = entity_type not in ("", "person", "human")
    if is_org and ground_truth.get("provided_metadata"):
        return False
    return True


# Metadata that constitutes real evidence ABOUT the candidate profile. "username"
# is excluded on purpose: it is derived from the URL string itself, so it proves
# nothing. Everything here had to come back from a search or a page fetch.
_EVIDENCE_META_KEYS = (
    "serper_title", "serper_snippet", "serper_results", "knowledge_graph",
    "bio", "display_name", "followers", "subscribers", "likes", "verified",
    "website", "title", "snippet",
)


def _candidate_has_evidence(cand: Dict[str, Any]) -> bool:
    """True when we actually retrieved something about this profile."""
    meta = cand.get("meta") or {}
    return any(meta.get(k) not in ("", None, [], {}) for k in _EVIDENCE_META_KEYS)


@dataclass
class VerificationResult:
    platform: str
    best_candidate: str = ""
    status: str = STATUS_NOT_FOUND
    confidence: int = 0
    reason: str = ""
    evidence: List[Any] = field(default_factory=list)
    rejected: List[Any] = field(default_factory=list)
    decision: str = ""


def is_configured() -> bool:
    """Configured if EITHER provider has a key (Anthropic primary, OpenAI fallback)."""
    return bool(ANTHROPIC_API_KEY or OPENAI_API_KEY)


def status_from_decision(decision: str, confidence: int, has_candidate: bool) -> str:
    """Map the LLM decision + confidence to a verification label."""
    if not has_candidate:
        return STATUS_NOT_FOUND
    d = (decision or "").strip().lower()
    if d.startswith("verif"):
        # Verified only at high confidence, else downgrade to manual review.
        return STATUS_VERIFIED if confidence >= _VERIFIED_MIN_CONFIDENCE else STATUS_MANUAL
    if d.startswith("wrong") or d in ("incorrect", "reject", "rejected", "no"):
        return STATUS_WRONG
    return STATUS_MANUAL  # manual_review / review / anything ambiguous


_SYSTEM_MSG = (
    "You are a meticulous social-media profile verification analyst. You do NOT "
    "have web access and must NEVER assume you can look anything up. Reason ONLY "
    "over the structured evidence provided in the user message.\n\n"
    "GOAL: decide which candidate URL (if any) is the official, authentic profile "
    "of the described person/entity on the given platform.\n\n"
    "Perform DEEP SEMANTIC comparison — never rely on username or display name "
    "alone. Compare the candidate's evidence against the ground-truth profile "
    "across: full name and aliases/nicknames, occupation/profession/industry, "
    "teams, employers, organizations, filmography, music, sport/league/position, "
    "biography, nationality, birth year/age, active years, education, awards, "
    "career keywords, official website, handle, follower plausibility, "
    "verification clues, and cross-references to known official profiles on other "
    "platforms.\n\n"
    "If the Wikipedia-derived fields are sparse, use 'provided_metadata' (from the "
    "input file) as the identity description. Treat 'reference_sources' "
    "(IMDb/Spotify/TMDb) as corroboration.\n\n"
    "CRITICAL — DECLARED HANDLES ARE ONLY HINTS, NOT PROOF: the handles in "
    "'known_official_profiles' (Wikidata-declared) and any handle inside "
    "'provided_metadata' (from the input file) can be STALE, REASSIGNED to someone "
    "else, or simply WRONG. A candidate whose URL/handle merely MATCHES one of these "
    "declared handles is NOT verified on that basis alone. You must ALSO confirm, from "
    "the CANDIDATE'S OWN evidence (title, snippet, bio, knowledge_graph, content) that "
    "it is the SAME specific person/entity — same occupation, domain/industry and "
    "notable works — with no contradicting signal. If the candidate's own content does "
    "not independently establish this, you have NOT verified it.\n\n"
    "CROSS-PLATFORM CONFIRMATION: if 'verified_handles_on_other_platforms' is present, "
    "those handles were ALREADY confirmed as this same entity's official profiles on "
    "OTHER platforms during this very check. A candidate whose handle matches (or is a "
    "clear variant of) one of them is strong corroboration — you may treat that "
    "cross-platform consistency as supporting evidence toward 'verified', provided no "
    "content contradicts the identity.\n\n"
    "CROSS-SOURCE AGREEMENT: a candidate flagged 'cross_source_agreement' was returned "
    "INDEPENDENTLY by two different discovery tools (a Serper web search AND Apify). "
    "That agreement makes it more likely to be the real, active profile and should "
    "RAISE your confidence that the link itself is genuine — but you MUST still confirm "
    "from the content and ground truth that it is the SAME person/entity before "
    "choosing 'verified'. Agreement supports the link; it does not by itself prove "
    "identity.\n\n"
    "LABELS:\n"
    "  • verified — use when the candidate's own content reasonably shows this is the "
    "person/entity's official profile: the name PLUS at least one substantive identity "
    "signal (occupation, domain/industry, or notable works) align, and NOTHING "
    "contradicts it. You do NOT need every field confirmed — a clear, uncontradicted "
    "identity match is enough, so do not withhold 'verified' merely because some "
    "details are missing. (A declared-handle/URL match with NO such content signal is "
    "still not enough on its own.)\n"
    "  • wrong — the candidate belongs to a DIFFERENT person/entity (including a "
    "same-name or same-handle namesake in a different field), is a post/video rather "
    "than a profile, is broken/redirected, or its content contradicts the ground "
    "truth. If the candidate's content points to a different domain than the ground "
    "truth (e.g. an athlete/sports account for an actor, a different company for a "
    "brand), label it wrong EVEN IF the handle matches a declared handle.\n"
    "  • manual_review — use for GENUINE uncertainty only: the content is too thin to "
    "tell who it is, several different people plausibly fit and you cannot distinguish "
    "which, or there is partial conflicting evidence. Do NOT use manual_review for a "
    "plausible, uncontradicted match a reasonable person would accept. A bare "
    "handle/URL match with no corroborating content is manual_review (not verified).\n\n"
    "MINIMISE FALSE POSITIVES: when unsure, choose manual_review, not verified. A "
    "blank/uncertain result is better than a wrong confirmation.\n\n"
    "best_candidate: set it to the single MOST LIKELY official profile among the "
    "candidates (even if you ultimately label it wrong or manual_review). Only "
    "leave it empty when NONE of the candidates could plausibly be this person. "
    "Never invent URLs — it must be exactly one of the provided candidate URLs, or "
    "an empty string.\n\n"
    "Confidence (0-100): give 90-100 when the name and at least one substantive identity "
    "signal (occupation/domain/works) align with no contradicting evidence — a clear, "
    "uncontradicted match earns 90+ even if some details are missing; 60-89 when you are "
    "genuinely torn between people or the evidence is too thin to tell; below 60 when "
    "unlikely or contradicted. Only 90+ is accepted as Verified downstream — reserve it "
    "for matches that are clear and uncontradicted, not merely a name/handle coincidence."
)


def _flatten_candidate(i: int, cand: dict) -> dict:
    """Build one candidate payload entry, surfacing all useful evidence."""
    meta = cand.get("meta", {}) or {}
    entry: Dict[str, Any] = {
        "id": i,
        "url": cand.get("url", ""),
        "source": cand.get("source", ""),
        "username": meta.get("username", ""),
        "display_name": meta.get("display_name", ""),
        "verified_badge": meta.get("verified", ""),
        "bio": meta.get("bio", ""),
        "website": meta.get("website", ""),
        "followers": meta.get("followers", ""),
        "following": meta.get("following", ""),
        "subscribers": meta.get("subscribers", ""),
        "likes": meta.get("likes", ""),
        # Serper-derived context.
        "serper_title": meta.get("serper_title", ""),
        "serper_snippet": meta.get("serper_snippet", ""),
        "knowledge_graph": meta.get("knowledge_graph", ""),
        # True when Serper AND Apify independently returned this same link.
        "cross_source_agreement": meta.get("found_by_serper_and_apify", ""),
    }
    # Fold in anything else observed (serper_results, dates, sitelinks, …).
    handled = set(entry) | {"image", "found_by_serper_and_apify"}
    extra = {k: v for k, v in meta.items() if k not in handled and v not in ("", None, [], {})}
    if extra:
        entry["other_metadata"] = extra
    return {k: v for k, v in entry.items() if v not in ("", None, [], {})}


def _build_user_message(platform: str, wiki_meta: Dict[str, Any], candidates: List[dict]) -> str:
    candidate_payload = [_flatten_candidate(i, c) for i, c in enumerate(candidates, start=1)]
    return (
        "GROUND-TRUTH PROFILE (Wikipedia + input metadata):\n"
        f"{json.dumps(wiki_meta, ensure_ascii=False, indent=2)}\n\n"
        f"PLATFORM: {platform}\n\n"
        "CANDIDATE PROFILES (rank these; choose at most one as best_candidate):\n"
        f"{json.dumps(candidate_payload, ensure_ascii=False, indent=2)}\n\n"
        "Return STRICT JSON only, with exactly these keys:\n"
        "{\n"
        '  "best_candidate": "<one candidate url, or empty string>",\n'
        '  "decision": "<verified|wrong|manual_review>",\n'
        '  "confidence": <integer 0-100>,\n'
        '  "reason": "<concise justification citing the matching/conflicting evidence>",\n'
        '  "evidence": ["<short supporting observation>", ...],\n'
        '  "rejected": [{"url": "<url>", "reason": "<why rejected>"}, ...]\n'
        "}"
    )


def _extract_json_obj(text: str) -> dict:
    if not text:
        raise ValueError("Empty LLM response.")
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object in LLM response.")
    return json.loads(text[start:end + 1])


def _call_anthropic(system_msg: str, user_msg: str) -> dict:
    """Primary LLM call — Anthropic (Claude) Messages API."""
    body = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 1024,
        # System prompt is a top-level field on the Messages API (not a message).
        "system": system_msg,
        "messages": [
            {"role": "user", "content": user_msg},
        ],
    }
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": _ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    response = request_with_retry("POST", _ANTHROPIC_URL, headers=headers, json=body, timeout=60)
    response.raise_for_status()
    blocks = response.json().get("content", []) or []
    content = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    return _extract_json_obj(content)


def _call_openai(system_msg: str, user_msg: str) -> dict:
    """Fallback LLM call — OpenAI Chat Completions."""
    body = {
        "model": OPENAI_CHAT_MODEL,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
    }
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    response = request_with_retry("POST", _OPENAI_URL, headers=headers, json=body, timeout=60)
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    return _extract_json_obj(content)


def _call_llm(system_msg: str, user_msg: str) -> dict:
    """
    Verify via the primary LLM (Anthropic), falling back to OpenAI if Anthropic
    is unavailable — no key, rate-limited/out of credit, or any other failure.
    """
    if ANTHROPIC_API_KEY:
        try:
            return _call_anthropic(system_msg, user_msg)
        except Exception as exc:  # noqa: BLE001 — fall back on any Anthropic failure
            if OPENAI_API_KEY:
                print(f"  [VERIFY] Anthropic unavailable ({exc.__class__.__name__}) "
                      f"— falling back to OpenAI.")
                return _call_openai(system_msg, user_msg)
            raise
    return _call_openai(system_msg, user_msg)


def verify_platform(
    platform: str,
    wiki_meta: Dict[str, Any],
    candidates: List[dict],
    is_person: bool = True,
) -> VerificationResult:
    """
    Verify candidate profiles for one platform in a single LLM request.
    Every platform (including Facebook) is judged purely by the LLM.
    Failures degrade to a Manual Review result rather than aborting the run.
    """
    if not candidates:
        return VerificationResult(platform=platform, status=STATUS_NOT_FOUND,
                                  reason="No candidate links to verify.")

    valid_urls = {c.get("url", "") for c in candidates if c.get("url")}

    if not is_configured():
        top = candidates[0].get("url", "")
        return VerificationResult(
            platform=platform, best_candidate=top, status=STATUS_MANUAL,
            confidence=0,
            reason="No LLM key set (ANTROPIC_API_KEY / OPENAI_API_KEY) — manual review required.",
        )

    user_msg = _build_user_message(platform, wiki_meta, candidates)
    try:
        parsed = _call_llm(_SYSTEM_MSG, user_msg)
    except requests.Timeout:
        print(f"  [VERIFY] {platform} timeout — flagged for manual review.")
        return VerificationResult(
            platform=platform, best_candidate=candidates[0].get("url", ""),
            status=STATUS_MANUAL, confidence=0, reason="LLM timeout.",
        )
    except Exception as exc:  # noqa: BLE001 — never abort the row on LLM failure
        print(f"  [VERIFY] {platform} LLM failure ({exc.__class__.__name__}: {exc}).")
        return VerificationResult(
            platform=platform, best_candidate=candidates[0].get("url", ""),
            status=STATUS_MANUAL, confidence=0, reason="LLM verification failed.",
        )

    best = str(parsed.get("best_candidate", "") or "").strip()
    try:
        confidence = int(round(float(parsed.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0
    confidence = max(0, min(100, confidence))
    reason = str(parsed.get("reason", "") or "").strip()
    evidence = parsed.get("evidence") or []
    rejected = parsed.get("rejected") or []
    decision = str(parsed.get("decision", "") or "").strip()

    # Guard: the model must return one of the supplied URLs (or empty).
    if best and best not in valid_urls:
        print(f"  [VERIFY] {platform} returned an unknown URL — discarding: {best}")
        best = ""
        confidence = min(confidence, 40)
        reason = f"Model returned a URL not in the candidate set. {reason}".strip()

    status = status_from_decision(decision, confidence, bool(best))

    # EVIDENCE FLOOR. A candidate we retrieved NOTHING about cannot be Verified,
    # however strong the ground truth is. Without this, a guessed or supplied
    # handle plus a rich Wikipedia profile reads as a confident match — and a URL
    # that 404s gets stamped Verified. Hit for real when Serper ran out of credits
    # and enrichment silently returned {} for every candidate.
    if status == STATUS_VERIFIED and best:
        chosen = next((c for c in candidates if c.get("url") == best), None)
        if chosen is not None and not _candidate_has_evidence(chosen):
            status = STATUS_MANUAL
            reason = (
                "No profile content could be retrieved for this candidate (search "
                "and profile lookup both returned nothing), so we cannot confirm it "
                "exists or belongs to this person. " + (reason or "")
            ).strip()
            print(f"  [VERIFY] {platform} evidence floor -> downgraded to manual review")

    # Name-only ground truth can't rule out a namesake — downgrade a Verified to
    # Manual Review (better a human check than a confident wrong confirmation).
    if status == STATUS_VERIFIED and _ground_truth_is_thin(wiki_meta):
        status = STATUS_MANUAL
        reason = (
            "Ground-truth identity is too thin (name only) to safely confirm — "
            "a namesake cannot be ruled out. " + (reason or "")
        ).strip()

    result = VerificationResult(
        platform=platform,
        best_candidate=best,
        status=status,
        confidence=confidence,
        reason=reason or "No confident match.",
        evidence=evidence if isinstance(evidence, list) else [evidence],
        rejected=rejected if isinstance(rejected, list) else [rejected],
        decision=decision,
    )
    print(f"  [VERIFY] {platform} -> {status} ({confidence}) | {best or '(none)'}")
    return result
