"""
Instagram private-account handling.

A private IG account can't be scraped or tracked. The profile page embeds
"is_private":true/false in its JSON (served to the OG-scraper UA). A DISCOVERED
private account is discarded outright; a verified or client-supplied private one
is kept and routed to Manual Review. A blocked fetch leaves no flag -> kept.
"""
from app.output import profile_metadata as pm
from app.pipeline import orchestrator as vp
from app.pipeline.orchestrator import VerificationResult

IG_PRIVATE = ('<html><head><title>Jane (&#064;jane) • Instagram</title>'
              '<meta property="og:title" content="Jane (@jane)">'
              '<meta property="og:description" content="120 Followers, 80 Following">'
              '</head><body><script>{"has_clips":true,"is_private":true}</script></body></html>')
IG_PUBLIC = ('<html><head><title>NASA (&#064;nasa) • Instagram</title>'
             '<meta property="og:title" content="NASA (@nasa)">'
             '<meta property="og:description" content="104M Followers, 94 Following">'
             '</head><body><script>{"has_clips":true,"is_private":false}</script></body></html>')


# ── Private-flag parse (fetch_profile_metadata is stubbed by conftest, so we
#    test the detection helper it uses) ─────────────────────────────────────────

def test_private_true_is_detected():
    assert pm._page_is_private(IG_PRIVATE) is True


def test_private_false_is_not_detected():
    assert pm._page_is_private(IG_PUBLIC) is False


def test_blocked_fetch_is_not_private():
    assert pm._page_is_private("") is False


# ── Discard filter ───────────────────────────────────────────────────────────

def _cand(url="https://www.instagram.com/x", source="serper", **meta):
    return {"url": url, "source": source, "meta": meta}


def test_discovered_private_is_discarded():
    kept = vp._filter_private_instagram([_cand(private=True)], "Instagram")
    assert kept == []


def test_verified_private_is_kept_and_flagged():
    cands = [_cand(private=True, verified=True)]
    kept = vp._filter_private_instagram(cands, "Instagram")
    assert len(kept) == 1 and kept[0].get(vp._PRIVATE_REVIEW_FLAG) is True


def test_client_supplied_private_is_kept_and_flagged():
    cands = [_cand(source="input", private=True)]
    kept = vp._filter_private_instagram(cands, "Instagram")
    assert len(kept) == 1 and kept[0].get(vp._PRIVATE_REVIEW_FLAG) is True


def test_public_account_is_kept_unflagged():
    cands = [_cand()]
    kept = vp._filter_private_instagram(cands, "Instagram")
    assert len(kept) == 1 and vp._PRIVATE_REVIEW_FLAG not in kept[0]


def test_filter_is_instagram_only():
    # A private flag on another platform must not drop the candidate here.
    cands = [_cand(url="https://www.facebook.com/x", source="serper", private=True)]
    assert vp._filter_private_instagram(cands, "Facebook") == cands


def test_mixed_pool_keeps_the_public_candidate():
    cands = [_cand(url="https://www.instagram.com/priv", private=True),
             _cand(url="https://www.instagram.com/real")]
    kept = vp._filter_private_instagram(cands, "Instagram")
    assert [c["url"] for c in kept] == ["https://www.instagram.com/real"]


# ── Manual routing ───────────────────────────────────────────────────────────

def test_chosen_private_verified_is_routed_to_manual():
    cand = _cand(url="https://www.instagram.com/jane", private=True, verified=True)
    [flagged] = vp._filter_private_instagram([cand], "Instagram")
    result = VerificationResult(platform="Instagram", best_candidate=flagged["url"],
                                status=vp.STATUS_VERIFIED, confidence=95)
    out = vp._route_private_to_manual(result, [flagged])
    assert out.status == vp.STATUS_MANUAL and out.confidence <= 60
    assert "private" in out.reason.lower()


def test_wrong_verdict_stands_even_if_private():
    cand = _cand(url="https://www.instagram.com/jane", private=True, verified=True)
    [flagged] = vp._filter_private_instagram([cand], "Instagram")
    result = VerificationResult(platform="Instagram", best_candidate=flagged["url"],
                                status=vp.STATUS_WRONG, confidence=20)
    out = vp._route_private_to_manual(result, [flagged])
    assert out.status == vp.STATUS_WRONG


def test_non_flagged_choice_is_untouched():
    cand = _cand(url="https://www.instagram.com/real")
    result = VerificationResult(platform="Instagram", best_candidate=cand["url"],
                                status=vp.STATUS_VERIFIED, confidence=95)
    out = vp._route_private_to_manual(result, [cand])
    assert out.status == vp.STATUS_VERIFIED


# ── Apify privacy flag (client-supplied input handle, read via Apify) ─────────

def test_apify_meta_maps_isPrivate_to_private():
    from app.discovery import apify as apify_service
    assert apify_service._get_meta({"username": "jane", "isPrivate": True}).get("private") is True
    assert apify_service._get_meta({"username": "nasa", "isPrivate": False}).get("private") is False


def _wiki_meta(name="Jane Doe"):
    from app.identity import wikipedia as wiki
    return wiki.WikiMetadata(talent=name, name=name)


def test_client_supplied_private_input_handle_routed_to_manual(monkeypatch):
    from app.discovery import apify as apify_service
    from app.pipeline import options as so
    monkeypatch.setattr(apify_service, "instagram_configured", lambda: True)
    monkeypatch.setattr(apify_service, "instagram_profile",
                        lambda url: {"display_name": "Jane Doe", "private": True})
    out = vp._validate_input_handle("Instagram", "https://www.instagram.com/janedoe",
                                    _wiki_meta(), so.DEFAULT, talent="Jane Doe")
    assert out.status == vp.STATUS_MANUAL
    assert out.best_candidate == "https://www.instagram.com/janedoe"
    assert "private" in out.reason.lower()


def test_client_supplied_public_input_handle_is_not_forced_to_manual(monkeypatch):
    # A public account with a name match must NOT be routed to Manual by this guard.
    from app.discovery import apify as apify_service
    from app.pipeline import options as so
    monkeypatch.setattr(apify_service, "instagram_configured", lambda: True)
    monkeypatch.setattr(apify_service, "instagram_profile",
                        lambda url: {"display_name": "Jane Doe", "private": False})
    monkeypatch.setattr(vp.verification_service, "verify_platform",
                        lambda *a, **k: VerificationResult(
                            platform="Instagram", best_candidate=a[2][0]["url"],
                            status=vp.STATUS_VERIFIED, confidence=95))
    out = vp._validate_input_handle("Instagram", "https://www.instagram.com/janedoe",
                                    _wiki_meta(), so.DEFAULT, talent="Jane Doe")
    assert out.status == vp.STATUS_VERIFIED
