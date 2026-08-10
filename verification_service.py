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
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

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


# Thin-ground-truth gate modes. Mirrors search_options.GATE_*; duplicated as
# plain strings so this module stays importable without the pipeline's config.
GATE_STRICT = "strict"
GATE_EVIDENCE = "evidence"

# In evidence mode, "probably" is not enough to stand in for a Wikipedia page.
_THIN_EVIDENCE_MIN_CONFIDENCE = max(
    0, min(100, int(os.environ.get("THIN_EVIDENCE_MIN_CONFIDENCE", "85"))))


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


# ────────────────────────────────────────────────────────────────────────────
#  Authenticity guards
#
#  A profile can describe the right person accurately and still not BE that
#  person. Fan pages quote real credits; tribute accounts state real facts.
#  Everything below separates "this content is ABOUT them" from "this account
#  IS them" — the distinction the LLM alone was not reliably making.
#
#  All guards are one-directional: they can only downgrade Verified to Manual
#  Review, never promote. A false trigger costs coverage, never precision.
# ────────────────────────────────────────────────────────────────────────────

# Phrases where an account DECLARES ITSELF to be about someone rather than
# authored by them. Deliberately narrow: plain third person is not enough,
# because genuine official pages are routinely written in third person
# ("Guy Branum is a comedian best known for…" is his real page).
_THIRD_PARTY_PATTERNS = (
    r"\bthis (?:page|account|profile) is about\b",
    r"\b(?:page|account) (?:is )?dedicated to\b",
    r"\bfan[ _-]?(?:page|account|club|site)\b",
    r"\bunofficial\b",
    r"\bnot affiliated\b",
    r"\bnot (?:the )?official\b",
    r"\bparody\b",
    r"\btribute (?:page|account)\b",
    r"\bwe are not\b",
    # Same self-declaration in the languages these pages most often use.
    r"\b(?:essa|esta) p[áa]gina é sobre\b",
    r"\besta p[áa]gina es sobre\b",
    r"\bp[áa]gina de fãs\b",
)
_THIRD_PARTY_RE = re.compile("|".join(_THIRD_PARTY_PATTERNS), re.I)

# Metadata fields that carry the profile's own words.
_SELF_DESCRIPTION_KEYS = (
    "bio", "serper_snippet", "serper_title", "display_name", "title", "snippet",
)


def _candidate_text(cand: Dict[str, Any]) -> str:
    meta = cand.get("meta") or {}
    parts = [str(meta.get(k, "")) for k in _SELF_DESCRIPTION_KEYS]
    kg = meta.get("knowledge_graph")
    if isinstance(kg, dict):
        parts += [str(kg.get("description", "")), str(kg.get("title", ""))]
    return " ".join(p for p in parts if p)


def _third_party_framing(cand: Dict[str, Any]) -> str:
    """The self-declaration phrase found, or "" when the account speaks as itself."""
    m = _THIRD_PARTY_RE.search(_candidate_text(cand))
    return m.group(0) if m else ""


def _tokens(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 1]


def _is_ordered_subsequence(needle: List[str], haystack: List[str]) -> bool:
    it = iter(haystack)
    return all(tok in it for tok in needle)


def _name_order_mismatch(talent: str, cand: Dict[str, Any]) -> str:
    """
    Flag a displayed name that contains the talent's words in the WRONG ORDER.

    "Scully N James" carries both tokens of "James Scully", so a set-based match
    accepts it — but it is a different person. Reordering is flagged; INSERTION
    is not, so "Anthony Charles Edwards" and "Toby Kebbell (actor)" still pass.
    """
    meta = cand.get("meta") or {}
    shown = str(meta.get("display_name") or meta.get("serper_title") or meta.get("title") or "")
    if not shown:
        return ""
    want, have = _tokens(talent), _tokens(shown)
    if len(want) < 2 or not set(want).issubset(set(have)):
        return ""  # not a full name match at all — a different guard's problem
    if _is_ordered_subsequence(want, have):
        return ""  # correct order, possibly with middle names — fine
    return shown.strip()[:60]


# Handle shapes that fan and impostor accounts favour. A penalty signal, not a
# rejection: plenty of real people have a digit in their handle.
_FAN_HANDLE_RE = re.compile(
    r"(?:^|[._-])(?:fan|fans|fanpage|official_?page|tribute|updates?|daily|source|news)"
    r"(?:[._-]|\d|$)|[._-]?\d{1,3}$",
    re.I,
)

# Evidence that is genuinely about the account holder rather than a search blurb.
_STRONG_EVIDENCE_KEYS = ("bio", "knowledge_graph", "verified", "followers", "subscribers")


def _has_strong_evidence(cand: Dict[str, Any]) -> bool:
    meta = cand.get("meta") or {}
    return any(meta.get(k) not in ("", None, [], {}) for k in _STRONG_EVIDENCE_KEYS)


_THIN_STRICT_REASON = (
    "Ground-truth identity is too thin (name only) to safely confirm — "
    "a namesake cannot be ruled out."
)


def _thin_gate_block(thin_gate: str, chosen: Optional[Dict[str, Any]],
                     confidence: int) -> str:
    """
    Decide what a thin ground truth costs. Returns a downgrade reason, or "".

    ``"strict"`` is Wikipedia mode and refuses every thin Verify, which is the
    behaviour that produced the measured precision — it must not change.

    ``"evidence"`` is custom (non-Wikipedia) mode. Those subjects have no
    Wikipedia page *by definition*, so the strict gate would send 100% of the
    run to Manual Review and the mode would be pointless. Instead of dropping
    the gate, the burden of proof moves onto the candidate itself: the profile
    must carry real fetched evidence (a bio, a follower count, a knowledge
    panel — not just a URL that parsed), and the model must be clearly, not
    marginally, confident. A weak or bare candidate still goes to a human.

    This is a deliberate precision trade: custom mode will confirm some profiles
    Wikipedia mode would have escalated. Every other guard still applies.
    """
    if thin_gate != GATE_EVIDENCE:
        return _THIN_STRICT_REASON
    if chosen is None or not _has_strong_evidence(chosen):
        return ("No Wikipedia ground truth, and the profile returned no "
                "substantive evidence (bio, following, knowledge panel) to "
                "confirm identity on.")
    if confidence < _THIN_EVIDENCE_MIN_CONFIDENCE:
        return (f"No Wikipedia ground truth, and confidence ({confidence}) is "
                f"below the {_THIN_EVIDENCE_MIN_CONFIDENCE} required to confirm "
                f"without one.")
    return ""


def _implausible_following(ground_truth: Dict[str, Any], cand: Dict[str, Any]) -> str:
    """
    A notable subject with a three-figure following is almost never the real
    account. Only applied when the ground truth shows genuine notability, so
    unknown people are unaffected.
    """
    notable = bool(ground_truth.get("known_works") or ground_truth.get("reference_sources")
                   or ground_truth.get("summary"))
    if not notable:
        return ""
    raw = str((cand.get("meta") or {}).get("followers", "")).strip()
    if not raw or re.search(r"[kmb]", raw, re.I):
        return ""  # K/M/B suffix means it is comfortably above the floor
    try:
        count = int(re.sub(r"[^\d]", "", raw) or 0)
    except ValueError:
        return ""
    return raw if 0 < count < 1000 else ""


def _authenticity_block(talent: str, ground_truth: Dict[str, Any],
                        cand: Dict[str, Any]) -> str:
    """Return a human-readable reason to withhold Verified, or "" to allow it."""
    phrase = _third_party_framing(cand)
    if phrase:
        return (f"The profile describes itself in the third party (\"{phrase}\"), which "
                f"indicates a fan, tribute or unofficial page rather than the person's own account.")

    shown = _name_order_mismatch(talent, cand)
    if shown:
        return (f"The displayed name \"{shown}\" uses the subject's words in a different order, "
                f"which commonly indicates a different person rather than a name variant.")

    followers = _implausible_following(ground_truth, cand)
    if followers:
        return (f"A follower count of {followers} is implausible for this subject, "
                f"suggesting an impostor or inactive duplicate account.")

    handle = str((cand.get("meta") or {}).get("username", "")) or _handle_from(cand.get("url", ""))
    if handle and _FAN_HANDLE_RE.search(handle) and not _has_strong_evidence(cand):
        return (f"The handle \"{handle}\" follows a fan/duplicate account pattern and no "
                f"profile content was retrieved to rule that out.")
    return ""


def _handle_from(url: str) -> str:
    m = re.search(r"(?:https?://)?[^/]+/(?:@|c/|channel/|user/)?([^/?#]+)", url or "")
    return m.group(1) if m else ""


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
    "FIRST-PARTY TEST — THE MOST COMMON FAILURE: an account can describe the person "
    "completely accurately and still not belong to them. Fan pages, tribute pages and "
    "news accounts quote real credits, real biographies and real roles — that is what "
    "they exist to do. So matching content proves the account is ABOUT the person; it "
    "does NOT prove the account IS the person. Ask specifically: does this profile speak "
    "AS the subject (first-person bio, their own links, their own promotional posts, a "
    "platform verification badge), or ABOUT the subject (describing them in the third "
    "party, 'this page is about…', 'unofficial', 'fan page', collecting their news)? "
    "Only the former supports 'verified'. Listing one's own projects or employers counts "
    "as speaking AS the subject; describing the person to an audience does not.\n\n"
    "ABSENCE OF CONTRADICTION IS NOT EVIDENCE: on platforms that expose little data "
    "(X, TikTok) you will often see only a handle and a name. 'Nothing contradicts this' "
    "is the default state of an empty profile, not a reason to verify. Verification "
    "requires a POSITIVE identity signal, never merely the lack of a negative one.\n\n"
    "NAME ORDER MATTERS: a displayed name containing the subject's words in a different "
    "order ('Scully N James' for 'James Scully') usually indicates a different person, "
    "not a stylisation. Extra middle names or suffixes are fine; reordering is not.\n\n"
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
    thin_gate: str = GATE_STRICT,
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

    # AUTHENTICITY GUARDS. An accurate description of the subject is not proof of
    # authorship — fan pages quote real credits. These catch the cases the model
    # read as confirmation: self-declared "about" pages, reordered names, and
    # impossible follower counts.
    if status == STATUS_VERIFIED and best:
        chosen = next((c for c in candidates if c.get("url") == best), None)
        if chosen is not None:
            block = _authenticity_block(
                str(wiki_meta.get("name") or ""), wiki_meta, chosen
            )
            if block:
                status = STATUS_MANUAL
                confidence = min(confidence, 70)
                reason = f"{block} {reason}".strip()
                print(f"  [VERIFY] {platform} authenticity guard -> manual review")

    # Name-only ground truth can't rule out a namesake — downgrade a Verified to
    # Manual Review (better a human check than a confident wrong confirmation).
    if status == STATUS_VERIFIED and _ground_truth_is_thin(wiki_meta):
        chosen = next((c for c in candidates if c.get("url") == best), None)
        block = _thin_gate_block(thin_gate, chosen, confidence)
        if block:
            status = STATUS_MANUAL
            reason = f"{block} {reason or ''}".strip()

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
