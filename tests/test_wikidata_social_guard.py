"""
Wikidata social-field guards.

Fix A — a social-ID value that is actually a URL, path, or bare domain (Wikidata
mis-files a website into the Facebook-ID field, e.g. UGA's P2013 = 'uga.edu') is
rejected rather than turned into a bogus 'facebook.com/uga.edu'.

Fix C — when the official website lists a DIFFERENT handle than Wikidata, it is
kept as a fallback candidate so a dead/wrong Wikidata handle ('upitt') can be
replaced by the site's real one ('h2pitt').
"""
from app.identity import wikidata as wd
from app.identity import wikipedia as wk
from app.pipeline import orchestrator as vp
from app.verification import verifier as vs


def _entity(**props):
    claims = {}
    for p, v in props.items():
        claims[p] = [{"mainsnak": {"snaktype": "value", "datavalue": {"value": v}}}]
    return {"claims": claims}


# ── Fix A ────────────────────────────────────────────────────────────────────

def test_domain_value_is_flagged_not_a_handle():
    # Bare-domain detection is platform-aware: a dotted TLD is a mis-filed domain on
    # a platform whose handles can't end in one (Facebook/X/YouTube).
    assert wd._social_value_is_not_a_handle("uga.edu", platform="Facebook") is True
    assert wd._social_value_is_not_a_handle("https://facebook.com/x") is True   # a URL (any platform)
    assert wd._social_value_is_not_a_handle("pages/Name/123") is True           # a path (any platform)
    assert wd._social_value_is_not_a_handle("uga.edu", website_host="uga.edu") is True


def test_real_handles_are_not_flagged():
    assert wd._social_value_is_not_a_handle("universityofga", platform="Facebook") is False
    assert wd._social_value_is_not_a_handle("john.smith", platform="Instagram") is False  # dotted handle
    assert wd._social_value_is_not_a_handle("@HowardU", platform="Facebook") is False


def test_dotted_tld_username_kept_on_instagram_and_tiktok():
    # Instagram/TikTok allow periods in real usernames, so a dotted-TLD value there
    # is a legitimate handle and must not be dropped — but the same value on Facebook
    # is still a mis-filed domain.
    assert wd._social_value_is_not_a_handle("studio.tv", platform="Instagram") is False
    assert wd._social_value_is_not_a_handle("studio.tv", platform="TikTok") is False
    assert wd._social_value_is_not_a_handle("studio.tv", platform="Facebook") is True


def test_website_in_facebook_field_is_dropped_others_kept():
    ent = _entity(P2013="uga.edu", P2003="universityofga",
                  P2002="universityofga", P856="https://uga.edu")
    soc = wd._extract_wikidata_socials(ent)
    assert "Facebook" not in soc                                        # garbage dropped
    assert soc["Instagram"][0] == "https://www.instagram.com/universityofga"
    assert soc["X"][0] == "https://x.com/universityofga"


def test_a_real_facebook_handle_still_resolves():
    ent = _entity(P2013="universityofga")
    soc = wd._extract_wikidata_socials(ent)
    assert soc["Facebook"][0].endswith("/universityofga")


# ── Fix C ────────────────────────────────────────────────────────────────────

def test_website_fallback_is_added_as_a_candidate(monkeypatch):
    meta = wk.WikiMetadata(talent="University of Pittsburgh",
                           name="University of Pittsburgh", found=True)
    meta.social_links = {"Facebook": "https://www.facebook.com/upitt"}      # Wikidata (stale)
    meta.website_social_links = {"Facebook": "https://www.facebook.com/h2pitt"}  # site (real)

    monkeypatch.setattr(vp, "_serper_primary_candidate", lambda *a, **k: ([], False))
    captured = {}

    def fake_verify(platform, wiki_meta, candidates, options=vp.search_options.DEFAULT):
        captured["urls"] = [c.get("url") for c in candidates]
        return vs.VerificationResult(platform=platform,
                                     best_candidate=candidates[-1]["url"],
                                     status=vs.STATUS_VERIFIED)

    monkeypatch.setattr(vp, "_verify", fake_verify)
    vp._resolve_platform_serper("University of Pittsburgh", "Facebook", meta)

    assert "https://www.facebook.com/upitt" in captured["urls"]     # Wikidata candidate
    assert "https://www.facebook.com/h2pitt" in captured["urls"]    # website fallback candidate
