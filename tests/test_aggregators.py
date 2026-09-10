"""
Phase 1 — following a link-aggregator (Linktree etc.) to recover the handles an
Instagram bio only points at instead of listing.

Guards:
  * Host detection is exact — a path that merely contains "linktr.ee" is not one.
  * Following is fail-soft: a blocked/empty aggregator yields {} and the caller
    falls through, exactly like a blocked bio.
  * The follower fills GAPS only — a handle the bio states outright is never
    overwritten by the aggregator's copy of it.
"""
import pytest

from app.discovery import aggregators as agg
from app.discovery import bio_links as bl

# A realistic aggregator page: lists the subject's five platform profiles.
LINKTREE_HTML = """
<html><body>
  <a href="https://www.instagram.com/shakira">Instagram</a>
  <a href="https://www.facebook.com/shakira">Facebook</a>
  <a href="https://www.youtube.com/@shakira">YouTube</a>
  <a href="https://x.com/shakira">X</a>
  <a href="https://www.tiktok.com/@shakira">TikTok</a>
  <a href="https://shakira.com">Website</a>
</body></html>
"""


# ── host detection ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,expected", [
    ("https://linktr.ee/shakira", True),
    ("https://www.beacons.ai/someone", True),
    ("https://bio.link/someone", True),
    ("https://komi.io/someone", True),
    ("https://www.instagram.com/someone", False),
    ("https://example.com/linktr.ee/fake", False),   # host is example.com
    ("", False),
])
def test_is_aggregator(url, expected):
    assert agg.is_aggregator(url) is expected


def test_aggregator_urls_in_finds_only_aggregators():
    html = ('<a href="https://linktr.ee/shakira">links</a>'
            '<a href="https://instagram.com/x">ig</a>'
            '<a href="https://beacons.ai/y">b</a>')
    assert agg.aggregator_urls_in(html) == [
        "https://linktr.ee/shakira", "https://beacons.ai/y"
    ]


# ── following ───────────────────────────────────────────────────────────────

def test_follow_extracts_platform_links(monkeypatch):
    agg.clear_cache()
    monkeypatch.setattr("app.output.profile_metadata._fetch_html",
                        lambda url: LINKTREE_HTML)
    out = agg.follow("https://linktr.ee/shakira")
    for p in ("Instagram", "Facebook", "YouTube", "X", "TikTok"):
        assert p in out, f"{p} missing from {out}"
    assert out["Instagram"].endswith("/shakira")


def test_follow_is_failsoft_on_error(monkeypatch):
    agg.clear_cache()

    def boom(url):
        raise RuntimeError("blocked")

    monkeypatch.setattr("app.output.profile_metadata._fetch_html", boom)
    assert agg.follow("https://linktr.ee/x") == {}


def test_follow_ignores_non_aggregator_urls():
    assert agg.follow("https://www.instagram.com/someone") == {}


def test_follow_can_exclude_the_anchor_platform(monkeypatch):
    agg.clear_cache()
    monkeypatch.setattr("app.output.profile_metadata._fetch_html",
                        lambda url: LINKTREE_HTML)
    out = agg.follow("https://linktr.ee/shakira", exclude_platform="Instagram")
    assert "Instagram" not in out
    assert "YouTube" in out


# ── integration: harvest follows a Linktree found in the anchor bio ──────────

def test_harvest_follows_linktree_in_instagram_bio(monkeypatch):
    """The Instagram case: the bio lists ONLY a Linktree; following it recovers
    the other four platforms."""
    agg.clear_cache(); bl.clear_cache()
    anchor_html = ('<html><body>bio '
                   '<a href="https://linktr.ee/shakira">my links</a>'
                   '</body></html>')

    def fake_fetch(url):
        return LINKTREE_HTML if "linktr.ee" in url else anchor_html

    monkeypatch.setattr("app.output.profile_metadata._fetch_html", fake_fetch)
    out = bl.harvest("https://www.instagram.com/shakira", "Instagram")
    # anchor's own platform (Instagram) is excluded; the rest come via Linktree
    for p in ("Facebook", "YouTube", "X", "TikTok"):
        assert p in out, f"{p} not recovered via Linktree: {out}"


def test_harvest_prefers_direct_bio_link_over_aggregator_copy(monkeypatch):
    """A handle stated outright in the bio wins over the aggregator's copy."""
    agg.clear_cache(); bl.clear_cache()
    anchor_html = ('<html><body>'
                   '<a href="https://www.youtube.com/@real_direct">YT</a>'
                   '<a href="https://linktr.ee/shakira">links</a>'
                   '</body></html>')

    def fake_fetch(url):
        return LINKTREE_HTML if "linktr.ee" in url else anchor_html

    monkeypatch.setattr("app.output.profile_metadata._fetch_html", fake_fetch)
    out = bl.harvest("https://www.instagram.com/shakira", "Instagram")
    assert "real_direct" in out["YouTube"]      # direct bio link, not the Linktree's
