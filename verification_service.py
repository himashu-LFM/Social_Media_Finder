"""
verification_service.py  —  LLM profile-verification engine
============================================================

Given a talent's structured Wikipedia metadata, a platform, and one or more
candidate profile URLs (each optionally carrying observed profile metadata),
ask the LLM to decide which candidate — if any — is the official profile.

Guarantees / rules:
  • The LLM NEVER searches the web. It reasons ONLY over the supplied data.
  • ALL candidates for a platform are sent in ONE request; the model ranks
    them and returns the single best (or none).
  • Output is strict JSON:
        {best_candidate, decision, confidence, reason, evidence, rejected}
  • Confidence (0-100) is mapped to a status band:
        95-100 -> Verified
        80-94  -> Likely Correct
        60-79  -> Needs Manual Review
        <60    -> Rejected
        (no usable candidate) -> Not Found

Uses the existing OpenAI Chat Completions convention (raw ``requests``,
temperature 0) so no new dependency is introduced.

Environment variables:
    OPENAI_API_KEY     OpenAI key (required for verification)
    OPENAI_CHAT_MODEL  Model id (default: gpt-4o-mini)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List

import requests

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_CHAT_MODEL = os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"

# Status bands.
STATUS_VERIFIED = "Verified"
STATUS_LIKELY = "Likely Correct"
STATUS_REVIEW = "Needs Manual Review"
STATUS_REJECTED = "Rejected"
STATUS_NOT_FOUND = "Not Found"


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


def status_from_confidence(confidence: int, has_candidate: bool) -> str:
    """Map a 0-100 confidence to a status band."""
    if not has_candidate:
        return STATUS_NOT_FOUND
    if confidence >= 95:
        return STATUS_VERIFIED
    if confidence >= 80:
        return STATUS_LIKELY
    if confidence >= 60:
        return STATUS_REVIEW
    return STATUS_REJECTED


_SYSTEM_MSG = (
    "You are a meticulous social-media profile verification analyst. "
    "You do NOT have web access and you must NEVER assume you can look anything up. "
    "Reason ONLY over the structured evidence provided in the user message. "
    "Your task: decide which candidate URL (if any) is the official, authentic "
    "profile of the described person/entity on the given platform.\n\n"
    "Weigh this observable evidence: username/handle similarity to the name and "
    "aliases, display name, profile bio, verified badge, links back to the official "
    "website, cross-references to other confirmed profiles, and consistency with the "
    "person's profession, nationality, known works, and aliases.\n\n"
    "If the Wikipedia-derived fields are sparse or empty, rely on the "
    "'provided_metadata' object (details supplied directly in the input file) as the "
    "primary identity description. Cross-reference candidates against "
    "'reference_sources' (IMDb/Spotify/TMDb) and 'known_official_profiles' "
    "(handles already confirmed on other platforms) when present.\n\n"
    "Each candidate includes fetched public profile evidence (username, "
    "display_name, verified, bio, website, followers) — use it as the main basis "
    "for your decision rather than the URL alone.\n\n"
    "CORE RULE: a blank result is better than a wrong profile. If no candidate is "
    "clearly the right person, return an empty best_candidate with low confidence. "
    "Never invent URLs — best_candidate must be exactly one of the provided candidate "
    "URLs, or an empty string.\n\n"
    "Confidence scale (0-100): 95-100 you are certain it is official; 80-94 very "
    "likely; 60-79 plausible but needs a human check; below 60 reject."
)


def _build_user_message(
    platform: str,
    wiki_meta: Dict[str, Any],
    candidates: List[dict],
) -> str:
    candidate_payload = []
    for i, cand in enumerate(candidates, start=1):
        meta = cand.get("meta", {}) or {}
        entry = {
            "id": i,
            "url": cand.get("url", ""),
            "source": cand.get("source", ""),
            # Fetched public profile evidence (flattened for prominence).
            "username": meta.get("username", ""),
            "display_name": meta.get("display_name", ""),
            "verified": meta.get("verified", ""),
            "bio": meta.get("bio", ""),
            "website": meta.get("website", ""),
            "followers": meta.get("followers", ""),
            "following": meta.get("following", ""),
            "search_title": cand.get("title", ""),
            "search_snippet": cand.get("snippet", ""),
        }
        # Include any extra observed fields not already flattened above.
        extra = {k: v for k, v in meta.items()
                 if k not in entry and k not in ("image",) and v not in ("", None)}
        if extra:
            entry["other_metadata"] = extra
        candidate_payload.append({k: v for k, v in entry.items() if v not in ("", None)})

    return (
        "PERSON / ENTITY (structured Wikipedia metadata):\n"
        f"{json.dumps(wiki_meta, ensure_ascii=False, indent=2)}\n\n"
        f"PLATFORM: {platform}\n\n"
        "CANDIDATE PROFILES (rank these; pick at most one):\n"
        f"{json.dumps(candidate_payload, ensure_ascii=False, indent=2)}\n\n"
        "Return STRICT JSON only, with exactly these keys:\n"
        "{\n"
        '  "best_candidate": "<one candidate url or empty string>",\n'
        '  "decision": "<verified|likely|review|rejected|not_found>",\n'
        '  "confidence": <integer 0-100>,\n'
        '  "reason": "<one concise sentence>",\n'
        '  "evidence": ["<short supporting observation>", ...],\n'
        '  "rejected": [{"url": "<url>", "reason": "<why>"}, ...]\n'
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
    response = requests.post(_OPENAI_URL, headers=headers, json=body, timeout=60)
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    return _extract_json_obj(content)


def verify_platform(
    platform: str,
    wiki_meta: Dict[str, Any],
    candidates: List[dict],
) -> VerificationResult:
    """
    Verify candidate profiles for one platform in a single LLM request.

    ``candidates`` must be non-empty (callers should skip platforms with no
    candidates). Failures degrade to a ``Needs Manual Review`` result rather
    than aborting the run.
    """
    if not candidates:
        return VerificationResult(platform=platform, status=STATUS_NOT_FOUND,
                                  reason="No candidate links to verify.")

    valid_urls = {c.get("url", "") for c in candidates if c.get("url")}

    if not is_configured():
        # No LLM available: surface the top candidate for manual review.
        top = candidates[0].get("url", "")
        return VerificationResult(
            platform=platform, best_candidate=top, status=STATUS_REVIEW,
            confidence=0, reason="OPENAI_API_KEY not set — manual review required.",
        )

    user_msg = _build_user_message(platform, wiki_meta, candidates)
    try:
        parsed = _call_openai(_SYSTEM_MSG, user_msg)
    except requests.Timeout:
        print(f"  [VERIFY] {platform} timeout — flagged for manual review.")
        return VerificationResult(
            platform=platform, best_candidate=candidates[0].get("url", ""),
            status=STATUS_REVIEW, confidence=0, reason="LLM timeout.",
        )
    except Exception as exc:  # noqa: BLE001 — never abort the row on LLM failure
        print(f"  [VERIFY] {platform} LLM failure ({exc.__class__.__name__}: {exc}).")
        return VerificationResult(
            platform=platform, best_candidate=candidates[0].get("url", ""),
            status=STATUS_REVIEW, confidence=0, reason="LLM verification failed.",
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

    has_candidate = bool(best)
    status = status_from_confidence(confidence, has_candidate)

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
