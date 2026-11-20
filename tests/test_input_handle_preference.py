"""
Lever #1: on a RE-VERIFIED platform (FB/X/TikTok), when the client supplied a
handle and verification landed on a DIFFERENT account, keep the client's handle IF
it independently verifies — otherwise leave discovery's pick untouched (so a stale
or wrong supplied handle is never forced through). IG/YT are untouched.
"""
from app.pipeline import orchestrator as vp
from app.pipeline import options as so
from app.identity import wikipedia as wiki

GT = wiki.WikiMetadata(talent="Mike Lawler", name="Mike Lawler")
INPUT = "https://www.facebook.com/lawler4ny"          # client-supplied (= BDR truth)
DISCOVERED = "https://www.facebook.com/RepMikeLawler"  # a different 'official' page


def _cands():
    return [
        {"url": INPUT, "source": "input", "meta": {"supplied_in_client_record": True}},
        {"url": DISCOVERED, "source": "serper", "meta": {}},
    ]


def _verified(url):
    return vp.VerificationResult(platform="Facebook", best_candidate=url,
                                 status=vp.STATUS_VERIFIED, confidence=90, decision="verified")


def _manual(url):
    return vp.VerificationResult(platform="Facebook", best_candidate=url,
                                 status=vp.STATUS_MANUAL, confidence=50, decision="manual_review")


def _norm(u):
    return vp.social_urls.normalize_profile_url(u, "Facebook").lower()


def test_prefers_supplied_handle_when_it_verifies(monkeypatch):
    # The lone-input re-verify confirms the client's handle -> it is kept.
    monkeypatch.setattr(vp, "_verify",
                        lambda platform, gt, cands, options=None, **k: _verified(cands[0]["url"]))
    out = vp._prefer_supplied_handle(_verified(DISCOVERED), "Facebook", INPUT, _cands(), GT, so.DEFAULT)
    assert out.status == vp.STATUS_VERIFIED
    assert _norm(out.best_candidate) == _norm(INPUT)
    assert "client handle kept" in (out.source or "").lower()


def test_keeps_discovery_when_supplied_handle_not_confirmable(monkeypatch):
    # Supplied handle can't verify alone (dead/wrong) -> discovery's pick stands.
    monkeypatch.setattr(vp, "_verify",
                        lambda platform, gt, cands, options=None, **k: _manual(cands[0]["url"]))
    out = vp._prefer_supplied_handle(_verified(DISCOVERED), "Facebook", INPUT, _cands(), GT, so.DEFAULT)
    assert out.best_candidate == DISCOVERED


def test_trusted_platform_is_untouched(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(vp, "_verify",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or _verified("x"))
    res = _verified("https://www.instagram.com/someoneelse")
    out = vp._prefer_supplied_handle(res, "Instagram",
                                     "https://www.instagram.com/mikelawler", _cands(), GT, so.DEFAULT)
    assert out is res and called["n"] == 0            # IG/YT: no-op, no extra verify


def test_no_known_url_is_a_noop():
    res = _verified(DISCOVERED)
    assert vp._prefer_supplied_handle(res, "Facebook", "", _cands(), GT, so.DEFAULT) is res


def test_already_on_supplied_handle_is_a_noop(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(vp, "_verify",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or _verified("x"))
    res = _verified(INPUT)
    out = vp._prefer_supplied_handle(res, "Facebook", INPUT, _cands(), GT, so.DEFAULT)
    assert out is res and called["n"] == 0            # no conflict -> no extra verify


def test_supplied_handle_not_among_candidates_is_a_noop(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(vp, "_verify",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or _verified("x"))
    only_discovered = [{"url": DISCOVERED, "source": "serper", "meta": {}}]
    out = vp._prefer_supplied_handle(_verified(DISCOVERED), "Facebook", INPUT, only_discovered, GT, so.DEFAULT)
    assert out.best_candidate == DISCOVERED and called["n"] == 0


def test_resolve_serper_wires_the_preference(monkeypatch):
    # End-to-end through _resolve_platform_serper: discovery picks the official page,
    # but the client's handle verifies alone, so the row keeps the client's handle.
    monkeypatch.setattr(vp, "_serper_primary_candidate",
                        lambda *a, **k: ([{"url": DISCOVERED, "source": "serper", "meta": {}}], False))
    monkeypatch.setattr(vp, "_enrich_candidates", lambda c, p: None)
    monkeypatch.setattr(vp, "_tag_backlinks", lambda *a, **k: None)
    monkeypatch.setattr(vp, "_tag_anchor_handle_matches", lambda *a, **k: None)

    def fake_verify(platform, gt, cands, options=None, **k):
        urls = [c["url"] for c in cands]
        if urls == [INPUT]:            # judging the lone client handle -> it verifies
            return _verified(INPUT)
        return _verified(DISCOVERED)   # full list -> discovery's official page
    monkeypatch.setattr(vp, "_verify", fake_verify)

    out = vp._resolve_platform_serper("Mike Lawler", "Facebook", GT, known_url=INPUT, options=so.DEFAULT)
    assert _norm(out.best_candidate) == _norm(INPUT)
