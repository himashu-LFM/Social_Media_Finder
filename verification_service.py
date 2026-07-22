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

Environment variables:
    OPENAI_API_KEY     OpenAI key (required for verification)
    OPENAI_CHAT_MODEL  Model id (default: gpt-4o-mini)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

from retry_util import request_with_retry

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_CHAT_MODEL = os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"

# Verification labels.
STATUS_VERIFIED = "Verified"
STATUS_WRONG = "Wrong"
STATUS_MANUAL = "Manual Review Needed"
STATUS_NOT_FOUND = "Not Found"

# A candidate may only be labelled Verified at or above this confidence.
_VERIFIED_MIN_CONFIDENCE = 90

# Facebook is held to a stricter bar: Verified requires a high follower/like
# count as hard evidence (name + profession are enforced by the prompt).
# Tunable via env; a non-Verified Facebook result becomes Wrong (or Not Found).
FACEBOOK_MIN_FOLLOWERS = float(os.environ.get("FACEBOOK_MIN_FOLLOWERS", "10000"))

_COUNT_UNITS = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def _to_number(value: Any) -> Optional[float]:
    """Parse a count like '2,450', '2.2K', '674M' into a float."""
    if value in (None, ""):
        return None
    m = re.search(r"([\d][\d.,]*)\s*([KMB])?", str(value), re.I)
    if not m:
        return None
    try:
        num = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return num * _COUNT_UNITS.get((m.group(2) or "").lower(), 1)


def _follower_count(meta: Dict[str, Any]) -> Optional[float]:
    """Best follower/like/subscriber count from a candidate's own evidence."""
    best: Optional[float] = None
    text = " ".join(str(meta.get(k, "")) for k in
                    ("serper_title", "serper_snippet", "title", "snippet"))
    for m in re.finditer(r"([\d][\d.,]*\s*[KMB]?)\s*(followers|likes|fans|subscribers)",
                         text, re.I):
        n = _to_number(m.group(1))
        if n is not None and (best is None or n > best):
            best = n
    for key in ("followers", "likes", "subscribers"):
        n = _to_number(meta.get(key))
        if n is not None and (best is None or n > best):
            best = n
    return best


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
    return bool(OPENAI_API_KEY)


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
    "(IMDb/Spotify/TMDb) and 'known_official_profiles' as strong corroboration.\n\n"
    "LABELS:\n"
    "  • verified — high confidence this is the person's official profile; metadata "
    "strongly matches; no contradictory evidence.\n"
    "  • wrong — the candidate belongs to a different person, is a post/video rather "
    "than a profile, is broken/redirected, or its metadata contradicts the "
    "ground truth.\n"
    "  • manual_review — evidence is mixed or partial, multiple people share the "
    "identity, or confidence is insufficient to confirm or reject.\n\n"
    "MINIMISE FALSE POSITIVES: when unsure, choose manual_review, not verified. A "
    "blank/uncertain result is better than a wrong confirmation.\n\n"
    "best_candidate: set it to the single MOST LIKELY official profile among the "
    "candidates (even if you ultimately label it wrong or manual_review). Only "
    "leave it empty when NONE of the candidates could plausibly be this person. "
    "Never invent URLs — it must be exactly one of the provided candidate URLs, or "
    "an empty string.\n\n"
    "Confidence (0-100): 90-100 certain; 60-89 plausible but not certain; below 60 "
    "unlikely/contradicted."
)

# Extra Facebook clause — person vs organization/brand.
_FACEBOOK_RULE_PERSON = (
    "\n\nSTRICT FACEBOOK RULE (person): choose decision='verified' ONLY when ALL of "
    "the following are simultaneously true: (1) the display name matches the person's "
    "name/aliases, (2) the bio/description matches the person's profession/career, "
    "and (3) the profile shows a HIGH follower/like count consistent with a public "
    "figure. If any one of these is missing or unproven, you MUST NOT verify — use "
    "decision='wrong'. Do NOT use manual_review for Facebook."
)
_FACEBOOK_RULE_ORG = (
    "\n\nSTRICT FACEBOOK RULE (organization/brand/network/franchise): choose "
    "decision='verified' ONLY when ALL of the following are simultaneously true: "
    "(1) the page name matches the entity's name or aliases, (2) the "
    "description/category matches the entity's industry/category, and (3) the page "
    "shows a HIGH follower/like count consistent with an official brand page. If any "
    "one is missing or unproven, you MUST NOT verify — use decision='wrong'. Do NOT "
    "use manual_review for Facebook."
)


def _system_message(platform: str, is_person: bool = True) -> str:
    if platform != "Facebook":
        return _SYSTEM_MSG
    return _SYSTEM_MSG + (_FACEBOOK_RULE_PERSON if is_person else _FACEBOOK_RULE_ORG)


def _apply_facebook_rule(
    status: str, confidence: int, reason: str, best: str, candidates: List[dict],
) -> tuple:
    """
    Deterministic Facebook guard: Verified requires a high follower/like count;
    any non-Verified Facebook result collapses to Wrong (or Not Found if blank).
    """
    if not best:
        return STATUS_NOT_FOUND, confidence, reason
    if status == STATUS_VERIFIED:
        meta = next((c.get("meta", {}) for c in candidates if c.get("url") == best), {})
        count = _follower_count(meta)
        if count is None or count < FACEBOOK_MIN_FOLLOWERS:
            shown = "absent" if count is None else str(int(count))
            note = (f" | Facebook rule: follower/like count ({shown}) below the "
                    f"required {int(FACEBOOK_MIN_FOLLOWERS)} — not verified.")
            return STATUS_WRONG, min(confidence, 55), reason + note
        return STATUS_VERIFIED, confidence, reason
    if status == STATUS_MANUAL:
        return STATUS_WRONG, confidence, (
            reason + " | Facebook requires name + profession/industry + high followers "
            "to verify; insufficient, marked Wrong."
        )
    return status, confidence, reason


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
    }
    # Fold in anything else observed (serper_results, dates, sitelinks, …).
    handled = set(entry) | {"image"}
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


def _call_openai(system_msg: str, user_msg: str) -> dict:
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


def verify_platform(
    platform: str,
    wiki_meta: Dict[str, Any],
    candidates: List[dict],
    is_person: bool = True,
) -> VerificationResult:
    """
    Verify candidate profiles for one platform in a single LLM request.
    ``is_person`` selects the person vs organization Facebook rule.
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
            confidence=0, reason="OPENAI_API_KEY not set — manual review required.",
        )

    user_msg = _build_user_message(platform, wiki_meta, candidates)
    try:
        parsed = _call_openai(_system_message(platform, is_person), user_msg)
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

    # Facebook is held to the strict, evidence-backed bar.
    if platform == "Facebook":
        status, confidence, reason = _apply_facebook_rule(
            status, confidence, reason, best, candidates
        )

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
