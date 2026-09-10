"""
Phase 3 — the Apify Instagram reader wired into custom-mode's bio-link phase.

Cascade order guarded here:
  trust client link (Verified) → free readers → Apify Instagram (gaps only) → ...
The Serper fallback runs after this phase and is unchanged, so it is not retested.

Guards:
  * Apify fills only the gaps the free readers left, and its links are Verified.
  * A platform the free read already found is NOT overwritten by Apify.
  * Apify is skipped when unconfigured or when no Instagram handle was supplied
    (so no paid call happens needlessly).
"""
import pytest

from app.pipeline import orchestrator as vp
from app.pipeline import options as so

CUSTOM = so.SearchOptions(mode="custom", prompt="social media handles")
IG = "https://www.instagram.com/shakira"


@pytest.fixture
def no_free_links(monkeypatch):
    """Simulate the common case: the anonymous Instagram read finds nothing."""
    monkeypatch.setattr(vp.bio_link_service, "harvest", lambda url, platform: {})


def _enable_apify(monkeypatch, links):
    monkeypatch.setattr(vp.apify_service, "instagram_configured", lambda: True)
    monkeypatch.setattr(vp.apify_service, "instagram_bio_links", lambda url: dict(links))


def test_apify_fills_gaps_and_marks_verified(no_free_links, monkeypatch):
    _enable_apify(monkeypatch, {
        "YouTube": "https://www.youtube.com/@shakira",
        "TikTok": "https://www.tiktok.com/@shakira",
    })
    out = vp._row_bio_link_phase("Shakira", {"Instagram": IG}, {}, CUSTOM)

    # client's own Instagram handle: trusted
    assert out["Instagram"].status == vp.STATUS_VERIFIED
    assert out["Instagram"].source == "Input file (Phase 0 anchor)"
    # gaps filled by Apify, Verified, correctly sourced
    for p in ("YouTube", "TikTok"):
        assert out[p].status == vp.STATUS_VERIFIED
        assert out[p].source == "Apify (Instagram bio)"


def test_apify_does_not_overwrite_a_free_read(monkeypatch):
    # free read already found YouTube directly
    monkeypatch.setattr(vp.bio_link_service, "harvest",
                        lambda url, platform: {"YouTube": "https://www.youtube.com/@free_direct"})
    _enable_apify(monkeypatch, {
        "YouTube": "https://www.youtube.com/@apify_copy",
        "TikTok": "https://www.tiktok.com/@shakira",
    })
    out = vp._row_bio_link_phase("Shakira", {"Instagram": IG}, {}, CUSTOM)
    assert "free_direct" in out["YouTube"].best_candidate      # free read wins
    assert out["YouTube"].source != "Apify (Instagram bio)"
    assert out["TikTok"].source == "Apify (Instagram bio)"     # gap still filled


def test_apify_skipped_when_unconfigured(no_free_links, monkeypatch):
    monkeypatch.setattr(vp.apify_service, "instagram_configured", lambda: False)

    def must_not_call(url):
        raise AssertionError("Apify must not be called when unconfigured")

    monkeypatch.setattr(vp.apify_service, "instagram_bio_links", must_not_call)
    out = vp._row_bio_link_phase("Shakira", {"Instagram": IG}, {}, CUSTOM)
    assert set(out) == {"Instagram"}          # only the trusted client anchor


def test_apify_skipped_without_instagram_handle(monkeypatch):
    monkeypatch.setattr(vp.bio_link_service, "harvest",
                        lambda url, platform: {})

    def must_not_call(url):
        raise AssertionError("Apify must not be called without an IG handle")

    monkeypatch.setattr(vp.apify_service, "instagram_configured", lambda: True)
    monkeypatch.setattr(vp.apify_service, "instagram_bio_links", must_not_call)
    # only a YouTube handle supplied — no Instagram to read via Apify
    out = vp._row_bio_link_phase(
        "Someone", {"YouTube": "https://www.youtube.com/@someone"}, {}, CUSTOM)
    assert "Instagram" not in out
