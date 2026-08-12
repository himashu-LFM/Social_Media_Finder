"""
End-to-end label decisions, with the LLM response recorded rather than called.

The unit tests elsewhere check individual guards. These drive the whole chain —
LLM verdict -> authenticity guards -> thin-ground-truth gate -> final label —
because that chain is where the real errors happened: every fix so far was
correct in isolation and still produced the wrong cell, because a later stage
overruled it.

Each case is a cell from a real run, with the candidate metadata and the model's
actual verdict as observed. No network, no API cost, runs in milliseconds.
"""
import pytest

import verification_service as vs

# Ground truth for a non-Wikipedia subject: a name and a broad category. This is
# genuinely all the client file provides for most of these rows.
THIN = {"name": "Meccha Bijin",
        "provided_metadata": {"title_category": "Talent"},
        "entity_type": "person or group (unspecified)"}

RICH = {"name": "Guy Branum", "professions": ["comedian", "writer"],
        "known_works": ["Talk Show the Game Show"], "entity_type": "person"}


@pytest.fixture
def llm(monkeypatch):
    """Record what the model said; skip the network."""
    def _set(decision, confidence, best, reason="recorded"):
        monkeypatch.setattr(vs, "_call_llm", lambda *_a, **_k: {
            "best_candidate": best, "decision": decision,
            "confidence": confidence, "reason": reason,
            "evidence": [], "rejected": [],
        })
    monkeypatch.setattr(vs, "is_configured", lambda: True)
    return _set


def label(candidates, gt=THIN, gate=vs.GATE_EVIDENCE, platform="X"):
    return vs.verify_platform(platform, gt, candidates, thin_gate=gate)


# ── the Pa-Om case: rejected for being more specific than a thin record ─────

def test_a_thin_record_cannot_support_a_wrong_verdict(llm):
    """
    Pa-Om 99999's real YouTube channel — bio literally '公式 … 4人組バンド'
    ('official … 4-member band') — came back Wrong at 30. With a name-only
    record we are not entitled to assert the profile belongs to someone else.
    """
    cand = {"url": "https://www.youtube.com/@pa_o_mu_99999",
            "meta": {"bio": "ผ้าอ้อม99999 公式 東京を拠点に活動する4人組バンド",
                     "display_name": "パーオーム99999"}}
    llm("wrong", 30, cand["url"], "describes a band, not an individual talent")
    r = label([cand], gt={**THIN, "name": "Pa-Om 99999"}, platform="YouTube")
    assert r.status == vs.STATUS_MANUAL
    assert "more specific than our name-only record" in r.reason


def test_a_real_mismatch_is_still_labelled_wrong(llm):
    """The downgrade must not neuter genuine rejections — AM:PM's cocktail bar."""
    cand = {"url": "https://www.instagram.com/kolkata_bar_co",
            "meta": {"bio": "Cocktail bar in Kolkata. Open 6pm-2am."}}
    llm("wrong", 20, cand["url"], "a bar, not the musician")
    r = label([cand], gt={**THIN, "name": "AM:PM"}, platform="Instagram")
    assert r.status == vs.STATUS_WRONG


def test_a_rich_record_keeps_its_wrong_verdict(llm):
    """Wikipedia mode is untouched: with real facts, 'wrong' means wrong."""
    cand = {"url": "https://x.com/guybranum", "meta": {"bio": "Pro cyclist"}}
    llm("wrong", 30, cand["url"])
    assert label([cand], gt=RICH, gate=vs.GATE_STRICT).status == vs.STATUS_WRONG


# ── the evidence gate: what may stand in for a Wikipedia page ───────────────

def test_an_unfetchable_profile_is_escalated_not_confirmed(llm):
    """X returns nothing to an anonymous fetch. A bare URL proves nothing."""
    cand = {"url": "https://x.com/mettya_bizin", "meta": {}}
    llm("verified", 95, cand["url"])
    assert label([cand]).status == vs.STATUS_MANUAL


def test_a_reused_client_handle_carries_the_cell(llm):
    """The client confirmed mettya_bizin on Instagram; the same handle on X."""
    cand = {"url": "https://x.com/mettya_bizin",
            "meta": {"anchor_handle_match": "mettya_bizin"}}
    llm("verified", 95, cand["url"])
    assert label([cand]).status == vs.STATUS_VERIFIED


def test_a_backlink_to_a_client_profile_carries_the_cell(llm):
    cand = {"url": "https://www.youtube.com/@mettya_bizin",
            "meta": {"backlink_to_client_profile": "https://www.instagram.com/mettya_bizin"}}
    llm("verified", 95, cand["url"])
    assert label([cand], platform="YouTube").status == vs.STATUS_VERIFIED


def test_the_profiles_own_search_snippet_counts(llm):
    """
    When the top search result IS this URL, the snippet is the page's own meta
    description — the bio by another route. Discarding it escalated correct
    profiles purely because we could not scrape the page.
    """
    cand = {"url": "https://x.com/mettya_bizin",
            "meta": {"serper_snippet": "めっちゃ美人です。バンドをしています。",
                     "serper_results": [{"link": "https://x.com/mettya_bizin"}]}}
    llm("verified", 95, cand["url"])
    assert label([cand]).status == vs.STATUS_VERIFIED


def test_a_third_party_blurb_does_not_count(llm):
    """A snippet from a news page describes the subject, not the account."""
    cand = {"url": "https://x.com/mettya_bizin",
            "meta": {"serper_snippet": "Meccha Bijin announce a new single",
                     "serper_results": [{"link": "https://musicnews.example/mb"}]}}
    llm("verified", 95, cand["url"])
    assert label([cand]).status == vs.STATUS_MANUAL


def test_a_marginal_verdict_is_escalated_even_with_evidence(llm):
    cand = {"url": "https://x.com/mettya_bizin",
            "meta": {"anchor_handle_match": "mettya_bizin", "bio": "musician"}}
    llm("verified", 80, cand["url"])
    assert label([cand]).status == vs.STATUS_MANUAL


# ── the guards still bite, whatever the new signals say ────────────────────

def test_a_fan_page_is_not_rescued_by_a_handle_match(llm):
    """The strongest new signal must not smuggle a fan page past the guards."""
    cand = {"url": "https://x.com/mettya_bizin",
            "meta": {"anchor_handle_match": "mettya_bizin",
                     "backlink_to_client_profile": "https://www.instagram.com/mettya_bizin",
                     "bio": "Fan page dedicated to Meccha Bijin. Not affiliated."}}
    llm("verified", 99, cand["url"])
    assert label([cand]).status == vs.STATUS_MANUAL


def test_a_reordered_name_is_still_caught(llm):
    cand = {"url": "https://x.com/scullyjames",
            "meta": {"display_name": "Scully N James", "bio": "actor",
                     "followers": "120K"}}
    llm("verified", 95, cand["url"])
    r = vs.verify_platform("X", {**THIN, "name": "James Scully"}, [cand],
                           thin_gate=vs.GATE_EVIDENCE)
    assert r.status == vs.STATUS_MANUAL


def test_confidence_below_the_bar_is_never_verified(llm):
    cand = {"url": "https://x.com/x", "meta": {"bio": "b", "followers": "1M"}}
    llm("verified", 60, cand["url"])
    assert label([cand], gt=RICH, gate=vs.GATE_STRICT).status != vs.STATUS_VERIFIED


# ── determinism ─────────────────────────────────────────────────────────────

def test_both_providers_pin_temperature():
    """
    A re-run of an identical file once changed ~24% of its cells. That is
    indistinguishable from being wrong 24% of the time, and it makes any
    before/after accuracy measurement meaningless.
    """
    import inspect
    for fn in (vs._call_anthropic, vs._call_openai):
        assert '"temperature": 0' in inspect.getsource(fn), fn.__name__
