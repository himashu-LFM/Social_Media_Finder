"""
Tester-confirmed change, item 1.

Only the client's Instagram and YouTube handles are trusted as-is. Facebook, X and
TikTok values in the BDR are re-verified like any discovered link:
  * they are NOT auto-Verified in Phase 0;
  * on a no-Wikipedia row they enter discovery as an ``input`` candidate, so they
    can be corroborated (e.g. a back-link to the trusted Instagram) — or, failing
    that, surfaced for Manual Review rather than silently dropped;
  * a client-supplied handle never drives cross-platform agreement by itself.
"""
from app.pipeline import options as so
from app.pipeline import orchestrator as vp
from app.verification import verifier as vs

CUSTOM = so.SearchOptions(mode="custom", prompt="social media handles")


# ── Phase 0: what stays trusted, what does not ───────────────────────────────

def test_facebook_x_tiktok_input_is_not_auto_verified(monkeypatch):
    monkeypatch.setattr(vp.bio_link_service, "harvest", lambda url, platform: {})
    monkeypatch.setattr(vp.apify_service, "instagram_configured", lambda: False)
    handles = {
        "Instagram": "https://www.instagram.com/shakira",
        "Facebook": "https://www.facebook.com/shakira",
        "X": "https://x.com/shakira",
        "TikTok": "https://www.tiktok.com/@shakira",
    }
    out = vp._row_bio_link_phase("Shakira", handles, {}, CUSTOM)
    # Only the trusted Instagram anchor is adopted; the other three fall through.
    assert set(out) == {"Instagram"}
    assert out["Instagram"].status == vs.STATUS_VERIFIED


def test_youtube_input_is_trusted_like_instagram(monkeypatch):
    monkeypatch.setattr(vp.bio_link_service, "harvest", lambda url, platform: {})
    monkeypatch.setattr(vp.apify_service, "instagram_configured", lambda: False)
    out = vp._row_bio_link_phase(
        "Some Creator", {"YouTube": "https://www.youtube.com/@somecreator"}, {}, CUSTOM)
    assert out["YouTube"].status == vs.STATUS_VERIFIED


# ── No-wiki discovery: the client handle enters as an "input" candidate ───────

def test_client_handle_is_injected_as_an_input_candidate(monkeypatch):
    monkeypatch.setattr(vp.serper_service, "is_configured", lambda: True)
    monkeypatch.setattr(vp.serper_service, "discover_by_site", lambda *a, **k: [])
    monkeypatch.setattr(vp, "_enrich_candidates", lambda cands, platform: None)

    cands, searched, errored = vp._gather_serper_candidates(
        "Some Client", "Facebook", [], "", "{name} site:{domain}", set(),
        client_url="https://www.facebook.com/someclient")

    assert [c["source"] for c in cands] == ["input"]
    assert cands[0]["url"].endswith("/someclient")


def test_unverified_client_facebook_is_flagged_not_dropped():
    """No back-link, no cross-platform agreement -> Manual Review, keeping the URL."""
    cands = [{"url": "https://www.facebook.com/someclient", "source": "input", "meta": {}}]
    res = vp._decide_no_wiki_platform(
        "Some Client", "Facebook", cands, searched=True, errored=False,
        known_profiles={"Instagram": "https://www.instagram.com/otherhandle"},
        anchor_slugs=["otherhandle"], mutual_handles=set())
    assert res.status == vs.STATUS_MANUAL
    assert res.best_candidate == "https://www.facebook.com/someclient"


def test_client_facebook_that_backlinks_to_trusted_instagram_is_verified():
    ig = "https://www.instagram.com/shakira"
    cands = [{
        "url": "https://www.facebook.com/shakira_official", "source": "input",
        "meta": {"profile_links": {"Instagram": ig}},
    }]
    res = vp._decide_no_wiki_platform(
        "Shakira", "Facebook", cands, searched=True, errored=False,
        known_profiles={"Instagram": ig}, anchor_slugs=["shakira"], mutual_handles=set())
    assert res.status == vs.STATUS_VERIFIED


def test_a_client_handle_alone_does_not_win_cross_platform_agreement():
    """An 'input' candidate must not be the top that mutual-handle agreement uses."""
    handle = "someclienthandle"
    cands = [{"url": f"https://x.com/{handle}", "source": "input", "meta": {}}]
    # Pretend the handle appears in the run's mutual set (as if agreed elsewhere).
    res = vp._decide_no_wiki_platform(
        "Some Client", "X", cands, searched=True, errored=False,
        known_profiles={}, anchor_slugs=[], mutual_handles={handle})
    # It is not auto-Verified off its own asserted handle — it is flagged instead.
    assert res.status == vs.STATUS_MANUAL


# ── No-wiki noise reduction: name-exact auto-verify (#2) + namesake gate (#1) ──

def _nw(talent, platform, cands, known=None, anchors=None):
    return vp._decide_no_wiki_platform(
        talent, platform, cands, searched=True, errored=False,
        known_profiles=known or {}, anchor_slugs=anchors or [], mutual_handles=set())


def test_name_exact_handle_is_surfaced_as_manual_not_auto_verified():
    # "Holly Wainwright" — TikTok handle is the name-exact '@hollywainwright'. A
    # name-exact handle is strong but unproven (could be a namesake or tribute page),
    # so it is surfaced at the TOP of Manual Review, not auto-Verified.
    cands = [{"url": "https://www.tiktok.com/@hollywainwright", "source": "serper", "meta": {}}]
    res = _nw("Holly Wainwright", "TikTok", cands,
              known={"Instagram": "https://www.instagram.com/wainwrightholly"},
              anchors=["wainwrightholly"])
    assert res.status == vs.STATUS_MANUAL
    assert res.best_candidate.endswith("hollywainwright")
    assert "name-exact" in (res.source or "").lower()
    assert "strong match" in (res.reason or "").lower()


def test_name_exact_does_not_match_fan_suffix_variant():
    # 'jessiestephens40' is NOT exactly "Jessie Stephens" -> stays Manual (name-plausible).
    cands = [{"url": "https://www.tiktok.com/@jessiestephens40", "source": "serper", "meta": {}}]
    res = _nw("Jessie Stephens", "TikTok", cands)
    assert res.status == vs.STATUS_MANUAL


def test_several_unrelated_namesakes_are_not_found():
    # The broad '<name> site:x.com' sweeps in multiple unrelated namesakes for
    # "Emily Vernem": none bears a name relation -> Not Found (not a human's time).
    cands = [{"url": "https://x.com/breguissimxs", "source": "serper", "meta": {}},
             {"url": "https://x.com/LeydeDa82339670", "source": "serper", "meta": {}}]
    res = _nw("Emily Vernem", "X", cands)
    assert res.status == vs.STATUS_NOT_FOUND


def test_lone_unrelated_hit_stays_manual_not_dropped():
    # Softened gate: a SINGLE uncorroborated, name-unrelated hit may be a real
    # account whose handle isn't the name (a podcast's network handle) -> keep it
    # as Manual for one human glance rather than hiding it as Not Found.
    cands = [{"url": "https://x.com/littlefishpod_", "source": "serper", "meta": {}}]
    res = _nw("Dyl & Friends Podcast", "X", cands)
    assert res.status == vs.STATUS_MANUAL
    assert res.best_candidate.endswith("littlefishpod_")


def test_name_plausible_hit_still_goes_to_manual():
    # '@RadioNational' shares the distinctive token 'radio'/'national' with
    # "ABC Radio National" -> worth a human -> Manual, not dropped.
    cands = [{"url": "https://x.com/RadioNational", "source": "serper", "meta": {}}]
    res = _nw("ABC Radio National", "X", cands)
    assert res.status == vs.STATUS_MANUAL
    assert res.best_candidate.endswith("RadioNational")


def test_client_supplied_handle_is_never_dropped_by_the_gate():
    # A client FB handle with no name relation still reaches a human (Manual), not Not Found.
    cands = [{"url": "https://www.facebook.com/xyz123feed", "source": "input", "meta": {}}]
    res = _nw("Some Brand", "Facebook", cands,
              known={"Instagram": "https://www.instagram.com/other"}, anchors=["other"])
    assert res.status == vs.STATUS_MANUAL
