"""
Phase 2 — the Apify Instagram cookieless reader.

Guards (all with the actor call mocked; no network, no credentials):
  * Reads the OTHER platforms directly listed in the bio links.
  * Follows a Linktree in the bio to recover handles IG lists only there.
  * Fail-soft: unconfigured / actor-raises / actor-error / empty → {} (never raises).
  * The anchor's own platform (Instagram) is never returned.
"""
import pytest

from app.discovery import apify as ap
from app.discovery import aggregators as agg

IG_URL = "https://www.instagram.com/shakira/"

# One dataset item whose bio lists platforms directly.
ITEM_DIRECT = {
    "username": "someone",
    "bio_links": [
        {"url": "https://www.youtube.com/@someone"},
        {"url": "https://www.tiktok.com/@someone"},
    ],
    "external_url": "https://twitter.com/someone",
    "biography": "musician",
}

# One dataset item whose bio lists ONLY a Linktree (the common Instagram case).
ITEM_LINKTREE = {
    "username": "shakira",
    "bio_links": [{"url": "https://linktr.ee/shakira"}],
    "external_url": None,
    "biography": "singer",
}

LINKTREE_HTML = """
<html><body>
  <a href="https://www.instagram.com/shakira">IG</a>
  <a href="https://www.facebook.com/shakira">FB</a>
  <a href="https://www.youtube.com/@shakira">YT</a>
  <a href="https://x.com/shakira">X</a>
  <a href="https://www.tiktok.com/@shakira">TT</a>
</body></html>
"""


@pytest.fixture
def configured(monkeypatch):
    """Make the reader think it's configured, without touching real env."""
    monkeypatch.setattr(ap, "APIFY_TOKEN", "test-token")
    monkeypatch.setattr(ap, "APIFY_INSTAGRAM_ACTOR_ID", "acme~ig-cookieless")
    agg.clear_cache()


def test_configured_flag(monkeypatch):
    monkeypatch.setattr(ap, "APIFY_TOKEN", "")
    monkeypatch.setattr(ap, "APIFY_INSTAGRAM_ACTOR_ID", "")
    assert ap.instagram_configured() is False
    monkeypatch.setattr(ap, "APIFY_TOKEN", "t")
    monkeypatch.setattr(ap, "APIFY_INSTAGRAM_ACTOR_ID", "a")
    assert ap.instagram_configured() is True


def test_unconfigured_returns_empty(monkeypatch):
    monkeypatch.setattr(ap, "APIFY_TOKEN", "")
    monkeypatch.setattr(ap, "APIFY_INSTAGRAM_ACTOR_ID", "")
    assert ap.instagram_bio_links(IG_URL) == {}


def test_reads_direct_bio_links(configured, monkeypatch):
    monkeypatch.setattr(ap, "_run_named_actor", lambda actor, inp: [ITEM_DIRECT])
    out = ap.instagram_bio_links(IG_URL)
    assert "YouTube" in out and out["YouTube"].endswith("/@someone")
    assert "TikTok" in out
    assert "X" in out                 # twitter.com normalised to X
    assert "Instagram" not in out     # anchor platform excluded


def test_follows_linktree_in_bio(configured, monkeypatch):
    monkeypatch.setattr(ap, "_run_named_actor", lambda actor, inp: [ITEM_LINKTREE])
    monkeypatch.setattr("app.output.profile_metadata._fetch_html",
                        lambda url: LINKTREE_HTML)
    out = ap.instagram_bio_links(IG_URL)
    for p in ("Facebook", "YouTube", "X", "TikTok"):
        assert p in out, f"{p} not recovered via Linktree: {out}"
    assert "Instagram" not in out


def test_failsoft_when_actor_raises(configured, monkeypatch):
    def boom(actor, inp):
        raise RuntimeError("actor down")
    monkeypatch.setattr(ap, "_run_named_actor", boom)
    assert ap.instagram_bio_links(IG_URL) == {}


def test_failsoft_on_actor_error_item(configured, monkeypatch):
    monkeypatch.setattr(ap, "_run_named_actor",
                        lambda actor, inp: [{"error": "PARSER_ERROR"}])
    assert ap.instagram_bio_links(IG_URL) == {}


def test_failsoft_on_empty_items(configured, monkeypatch):
    monkeypatch.setattr(ap, "_run_named_actor", lambda actor, inp: [])
    assert ap.instagram_bio_links(IG_URL) == {}


def test_input_template_override(monkeypatch):
    monkeypatch.setattr(
        ap, "APIFY_INSTAGRAM_INPUT",
        '{"directUrls": ["https://www.instagram.com/{username}/"]}')
    assert ap._instagram_input("shakira") == {
        "directUrls": ["https://www.instagram.com/shakira/"]
    }
    monkeypatch.setattr(ap, "APIFY_INSTAGRAM_INPUT", "")
    assert ap._instagram_input("shakira") == {"usernames": ["shakira"]}
