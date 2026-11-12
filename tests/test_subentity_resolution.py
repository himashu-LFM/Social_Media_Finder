"""
Gap #4 — campus / sub-entity resolution.

A college/university row often names a specific campus or branch (written with a
parenthesis, a comma, the word "at", or a "- SUNY" system suffix). The pipeline
used to let the parent's big account — or the system's, or a sibling campus's —
stand in for the campus. These tests pin the two pieces of the fix:

  * ``_subentity_identity`` — extracting the distinguishing qualifier + acronym.
  * ``_guard_subentity`` — re-pointing a Verified result to the campus account, or
    routing it to Manual Review when none of the candidates is the campus.

Plus a lock on the Wikidata ground-truth extraction: only the 5 target social
platforms are emitted, each to its own cell (an image/website claim is not one).

Everything here is pure-function and offline — no Serper, no network.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.pipeline.orchestrator import (  # noqa: E402
    _subentity_identity,
    _guard_subentity,
    VerificationResult,
    STATUS_VERIFIED,
    STATUS_MANUAL,
)


# ── helpers ──────────────────────────────────────────────────────────────────
def _cand(url: str, title: str = "", source: str = "serper") -> dict:
    return {"url": url, "source": source, "meta": {"title": title}}


def _verified(url: str, platform: str = "YouTube") -> VerificationResult:
    return VerificationResult(
        platform=platform, best_candidate=url, status=STATUS_VERIFIED,
        confidence=95, decision="verified",
    )


# ── _subentity_identity: qualifier extraction ────────────────────────────────
@pytest.mark.parametrize("name, qualifier, acronym", [
    # Parentheses
    ("Rutgers University (Newark) - DAR", {"newark"}, "run"),
    ("Rutgers University (Camden) - DAR", {"camden"}, "ruc"),
    ("Rutgers University (New Brunswick)", {"new", "brunswick"}, "runb"),
    ("University of Illinois (Chicago)", {"chicago"}, "uic"),
    # Comma
    ("University of California, Los Angeles", {"los", "angeles"}, "ucla"),
    ("University of California, Santa Barbara - DAR", {"santa", "barbara"}, "ucsb"),
    ("University of Maryland, College Park", {"college", "park"}, "umcp"),
    # " at "
    ("University at Buffalo - SUNY", {"buffalo"}, "ub"),
    # System suffix, no inner separator -> base's own distinctive tokens
    ("Stony Brook University - SUNY", {"stony", "brook"}, "sbu"),
    ("Binghamton University - SUNY", {"binghamton"}, "bu"),
])
def test_qualifier_extracted_for_each_campus_pattern(name, qualifier, acronym):
    q, a = _subentity_identity(name)
    assert q == qualifier
    assert a == acronym


@pytest.mark.parametrize("name", [
    "University of Pennsylvania - DAR",  # "- DAR" is a dataset tag, not a system
    "University of California",          # plain parent, no campus qualifier
    "Harvard University",
])
def test_plain_or_parent_org_has_no_qualifier(name):
    q, a = _subentity_identity(name)
    assert q == set()
    assert a == ""


@pytest.mark.parametrize("name", [
    "Martin Luther King, Jr.",   # comma-suffix on a PERSON — no institution word
    "Beyonce",
    "Coca-Cola",                 # hyphenated brand, not a campus
    "The Weeknd",
])
def test_non_institution_names_are_never_treated_as_campuses(name):
    # The guard engages only when the name reads as an educational institution,
    # so a person's "Name, Jr." can never look like a campus.
    assert _subentity_identity(name) == (set(), "")


# ── _guard_subentity: re-point or downgrade ──────────────────────────────────
def test_parent_account_alone_is_downgraded_to_manual():
    # Rutgers (Camden) with only the parent Rutgers channel discovered.
    result = _verified("https://www.youtube.com/user/Rutgers")
    cands = [_cand("https://www.youtube.com/user/Rutgers", "Rutgers University")]
    out = _guard_subentity(result, cands, "Rutgers University (Camden) - DAR", "YouTube")
    assert out.status == STATUS_MANUAL
    assert "camden" in out.reason.lower()
    # It never fabricates a different account — the surfaced link is unchanged.
    assert out.best_candidate == "https://www.youtube.com/user/Rutgers"


def test_repoints_to_the_campus_account_when_one_is_present():
    # Parent chosen, but the Camden campus account is in the candidate set.
    result = _verified("https://www.youtube.com/user/Rutgers")
    cands = [
        _cand("https://www.youtube.com/user/Rutgers", "Rutgers University"),
        _cand("https://www.youtube.com/@RutgersUniversityCamden", "Rutgers University-Camden"),
    ]
    out = _guard_subentity(result, cands, "Rutgers University (Camden) - DAR", "YouTube")
    assert out.status == STATUS_VERIFIED
    assert out.best_candidate == "https://www.youtube.com/@RutgersUniversityCamden"


def test_prefers_main_campus_over_a_sibling_sub_channel():
    # UCSB: the discovered winner was the news sub-channel; the main channel is
    # also present and carries the 'santa barbara' qualifier explicitly.
    result = _verified("https://www.youtube.com/user/UCSBNews")
    cands = [
        _cand("https://www.youtube.com/user/UCSBNews", "UCSB News"),
        _cand("https://www.youtube.com/@ucsantabarbara", "UC Santa Barbara"),
    ]
    out = _guard_subentity(
        result, cands, "University of California, Santa Barbara - DAR", "YouTube")
    assert out.status == STATUS_VERIFIED
    assert out.best_candidate == "https://www.youtube.com/@ucsantabarbara"


def test_system_account_is_downgraded_for_a_system_campus():
    # The SUNY system account must not stand in for Stony Brook.
    result = _verified("https://www.instagram.com/suny", platform="Instagram")
    cands = [_cand("https://www.instagram.com/suny", "SUNY")]
    out = _guard_subentity(result, cands, "Stony Brook University - SUNY", "Instagram")
    assert out.status == STATUS_MANUAL


def test_campus_acronym_handle_stays_verified():
    # @sbu is the campus's own acronym — it must NOT be downgraded.
    result = _verified("https://www.instagram.com/sbu", platform="Instagram")
    cands = [_cand("https://www.instagram.com/sbu", "Stony Brook University")]
    out = _guard_subentity(result, cands, "Stony Brook University - SUNY", "Instagram")
    assert out.status == STATUS_VERIFIED
    assert out.best_candidate == "https://www.instagram.com/sbu"


def test_campus_explicit_handle_stays_verified():
    # A handle carrying the qualifier token is accepted as-is.
    result = _verified("https://www.instagram.com/stonybrooku", platform="Instagram")
    cands = [_cand("https://www.instagram.com/stonybrooku", "Stony Brook")]
    out = _guard_subentity(result, cands, "Stony Brook University - SUNY", "Instagram")
    assert out.status == STATUS_VERIFIED


def test_plain_org_result_is_left_untouched():
    # "University of Pennsylvania - DAR" is a top-level org (no campus qualifier):
    # the guard must not touch an alias question that is not a sub-entity one.
    result = _verified("https://x.com/UofPenn", platform="X")
    cands = [_cand("https://x.com/UofPenn", "University of Pennsylvania")]
    out = _guard_subentity(result, cands, "University of Pennsylvania - DAR", "X")
    assert out.status == STATUS_VERIFIED
    assert out.best_candidate == "https://x.com/UofPenn"


def test_short_qualifier_substring_in_handle_is_not_evidence():
    # 'park' (from 'College Park') must NOT match a coincidental substring like
    # 'sparkle'; with no real campus signal the parent-style account is downgraded.
    result = _verified("https://www.instagram.com/marylandsparkle", platform="Instagram")
    cands = [_cand("https://www.instagram.com/marylandsparkle", "Maryland Sparkle")]
    out = _guard_subentity(result, cands, "University of Maryland, College Park", "Instagram")
    assert out.status == STATUS_MANUAL


def test_collapsed_youtube_handle_still_satisfies_campus_guard():
    # After YouTube de-dupe rewrites the URL to /channel/UC..., the campus qualifier
    # must still be found via the preserved ``collapsed_from_url`` handle.
    channel = "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv"
    result = _verified(channel)
    cands = [{
        "url": channel, "source": "serper",
        "meta": {"collapsed_from_url": "https://www.youtube.com/@RutgersUniversityCamden"},
    }]
    out = _guard_subentity(result, cands, "Rutgers University (Camden)", "YouTube")
    assert out.status == STATUS_VERIFIED
    assert out.best_candidate == channel


def test_non_verified_result_is_left_untouched():
    # The guard only acts on a Verified result; a Manual one is already surfaced.
    result = VerificationResult(
        platform="YouTube", best_candidate="https://www.youtube.com/user/Rutgers",
        status=STATUS_MANUAL, confidence=70,
    )
    cands = [_cand("https://www.youtube.com/user/Rutgers", "Rutgers University")]
    out = _guard_subentity(result, cands, "Rutgers University (Camden)", "YouTube")
    assert out.status == STATUS_MANUAL
    assert out.best_candidate == "https://www.youtube.com/user/Rutgers"


# ── Wikipedia/Wikidata ground truth: only the 5 platforms, correct cell ───────
def _value_claim(prop: str, value) -> dict:
    return {prop: [{"mainsnak": {"snaktype": "value",
                                 "datavalue": {"value": value}}}]}


def test_wikidata_extraction_keeps_only_the_five_social_cells():
    from app.identity import wikidata

    claims = {}
    claims.update(_value_claim("P2002", "stonybrooku"))         # X
    claims.update(_value_claim("P2003", "stonybrooku"))         # Instagram
    claims.update(_value_claim("P2397", "UCq_rvAr_pcWk3Zv1Sxx")) # YouTube channel
    claims.update(_value_claim("P7085", "stonybrooku"))         # TikTok
    claims.update(_value_claim("P2013", "stonybrook"))          # Facebook
    claims.update(_value_claim("P856", "https://www.stonybrook.edu"))  # website
    claims.update(_value_claim("P18", "StonyBrook.jpg"))        # image (must drop)

    socials = wikidata._extract_wikidata_socials({"claims": claims})

    # Each of the 5 target platforms maps to its own correct URL.
    assert socials["X"][0] == "https://x.com/stonybrooku"
    assert socials["Instagram"][0] == "https://www.instagram.com/stonybrooku"
    assert socials["YouTube"][0] == "https://www.youtube.com/channel/UCq_rvAr_pcWk3Zv1Sxx"
    assert socials["TikTok"][0] == "https://www.tiktok.com/@stonybrooku"
    assert socials["Facebook"][0] == "https://www.facebook.com/stonybrook"

    # The image claim is never surfaced as a platform cell; the website is kept
    # only under the internal "_website" key, not as one of the 5.
    assert "_image" not in socials
    assert "P18" not in socials
    social_platforms = {k for k in socials if not k.startswith("_")}
    assert social_platforms == {"X", "Instagram", "YouTube", "TikTok", "Facebook"}
