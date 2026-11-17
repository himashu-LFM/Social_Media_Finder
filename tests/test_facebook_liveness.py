"""
Facebook existence/liveness check.

Facebook 200s for every URL (a login wall), so a dead vanity like
facebook.com/uga.edu used to get Verified on name match. A real page carries an
OpenGraph title; a dead one returns the generic "Facebook" shell with none. The
check reads that difference, conservatively — a blocked/unreadable fetch is never
treated as dead, so a real page is never dropped on a flaky response.
"""
from app.output import profile_metadata as pm
from app.pipeline import orchestrator as vp

REAL = ('<html><head><title>University of Georgia | Athens GA</title>'
        '<meta property="og:title" content="University of Georgia | Athens GA">'
        '</head></html>')
DEAD = '<html><head><title>Facebook</title></head><body></body></html>'
WALL = '<html><head><title>Log in to Facebook</title></head></html>'


def test_fb_real_page_is_not_dead(monkeypatch):
    pm.clear_cache(); monkeypatch.setattr(pm, "_fetch_html", lambda u: REAL)
    assert pm._facebook_page_is_dead("https://www.facebook.com/universityofga") is False


def test_fb_dead_vanity_is_dead(monkeypatch):
    pm.clear_cache(); monkeypatch.setattr(pm, "_fetch_html", lambda u: DEAD)
    assert pm._facebook_page_is_dead("https://www.facebook.com/uga.edu") is True


def test_fb_blocked_or_empty_fetch_is_not_dead(monkeypatch):
    # The critical safety case: a failed/blocked fetch must NEVER be called dead.
    pm.clear_cache(); monkeypatch.setattr(pm, "_fetch_html", lambda u: "")
    assert pm._facebook_page_is_dead("https://www.facebook.com/anything") is False


def test_fb_login_wall_is_not_dead(monkeypatch):
    # A "Log in to Facebook" interstitial is not the generic dead shell -> keep.
    pm.clear_cache(); monkeypatch.setattr(pm, "_fetch_html", lambda u: WALL)
    assert pm._facebook_page_is_dead("https://www.facebook.com/realpage") is False


def _route_fb(monkeypatch):
    """Restore real FB routing over conftest's blanket profile_is_missing stub."""
    monkeypatch.setattr(vp.profile_metadata, "profile_is_missing",
                        lambda url, platform: platform == "Facebook"
                        and pm._facebook_page_is_dead(url))


def test_drop_missing_drops_a_dead_facebook_candidate(monkeypatch):
    pm.clear_cache(); monkeypatch.setattr(pm, "_fetch_html", lambda u: DEAD)
    _route_fb(monkeypatch)
    cands = [{"url": "https://www.facebook.com/uga.edu", "source": "serper", "meta": {}}]
    assert vp._drop_missing_profiles(cands, "Facebook") == []


def test_drop_missing_keeps_a_real_facebook_candidate(monkeypatch):
    pm.clear_cache(); monkeypatch.setattr(pm, "_fetch_html", lambda u: REAL)
    _route_fb(monkeypatch)
    cands = [{"url": "https://www.facebook.com/universityofga", "source": "serper", "meta": {}}]
    assert len(vp._drop_missing_profiles(cands, "Facebook")) == 1


# ── Instagram existence check (same og:title signal) ─────────────────────────

IG_REAL = ('<html><head><title>WashU (&#064;washu) &#x2022; Instagram photos and videos</title>'
           '<meta property="og:title" content="WashU (@washu)"></head></html>')
# A genuinely missing username: the generic shell PLUS an explicit not-found marker.
IG_DEAD = ('<html><head><title>Instagram</title></head><body>'
           "Sorry, this page isn't available. The link you followed may be broken, "
           "or the page may have been removed.</body></html>")
# A bare shell with no marker — what IG ALSO returns for a blocked/rate-limited view
# of a REAL profile, so it must NOT be treated as dead.
IG_SHELL = '<html><head><title>Instagram</title></head><body></body></html>'


def test_ig_real_profile_is_not_dead(monkeypatch):
    pm.clear_cache(); monkeypatch.setattr(pm, "_fetch_html", lambda u: IG_REAL)
    assert pm._instagram_page_is_dead("https://www.instagram.com/washu") is False


def test_ig_dead_username_is_dead(monkeypatch):
    pm.clear_cache(); monkeypatch.setattr(pm, "_fetch_html", lambda u: IG_DEAD)
    assert pm._instagram_page_is_dead("https://www.instagram.com/runewark") is True


def test_ig_generic_shell_is_not_dead(monkeypatch):
    # No og:title AND no not-found marker: indistinguishable from a blocked fetch of
    # a real profile, so the probe must keep it rather than drop a correct account.
    pm.clear_cache(); monkeypatch.setattr(pm, "_fetch_html", lambda u: IG_SHELL)
    assert pm._instagram_page_is_dead("https://www.instagram.com/realbutblocked") is False


def test_ig_blocked_fetch_is_not_dead(monkeypatch):
    pm.clear_cache(); monkeypatch.setattr(pm, "_fetch_html", lambda u: "")
    assert pm._instagram_page_is_dead("https://www.instagram.com/anything") is False


def test_drop_missing_drops_a_dead_instagram_candidate(monkeypatch):
    pm.clear_cache(); monkeypatch.setattr(pm, "_fetch_html", lambda u: IG_DEAD)
    monkeypatch.setattr(vp.profile_metadata, "profile_is_missing",
                        lambda url, platform: platform == "Instagram"
                        and pm._instagram_page_is_dead(url))
    cands = [{"url": "https://www.instagram.com/runewark", "source": "wikidata", "meta": {}}]
    assert vp._drop_missing_profiles(cands, "Instagram") == []


# ── pages parse once per run; the cache holds facts, not raw HTML ─────────────

def test_page_is_parsed_once_per_run(monkeypatch):
    """The liveness probe and enrichment hit the same FB/IG page; it must download
    once per URL per run. The per-run cache holds the small parsed facts, never the
    raw HTML (which would grow unbounded across a large run)."""
    pm.clear_cache()
    calls = {"n": 0}

    class _Resp:
        ok = True
        content = b"x"
        text = '<html><meta property="og:title" content="X"></html>'

    def _fake_get(method, url, **kw):
        calls["n"] += 1
        return _Resp()

    monkeypatch.setattr(pm, "request_with_retry", _fake_get)
    url = "https://www.facebook.com/somepage"
    first = pm._load_page(url)
    second = pm._load_page(url)
    assert first is second    # same cached facts object
    assert calls["n"] == 1    # downloaded once, shared by both callers


def test_failed_fetch_is_not_cached(monkeypatch):
    """A transient failure must not be frozen for the run as 'dead': a later call
    re-attempts the fetch rather than returning a cached miss."""
    pm.clear_cache()
    calls = {"n": 0}

    class _Resp:
        ok = False
        status_code = 503
        content = b""
        text = ""

    def _fake_get(method, url, **kw):
        calls["n"] += 1
        return _Resp()

    monkeypatch.setattr(pm, "request_with_retry", _fake_get)
    url = "https://www.facebook.com/blocked"
    assert pm._load_page(url) is None
    first_total = calls["n"]
    assert pm._load_page(url) is None
    assert calls["n"] > first_total   # not cached -> re-attempted
