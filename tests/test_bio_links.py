"""
Phase 0 — adopting first-party links from a client-supplied anchor profile.

Two things this file is really guarding:

1. Wikipedia mode never enters Phase 0. Every saving here comes from *skipping*
   verification, so a leak into the tuned flow would cost precision silently.
2. A harvest failure is a normal outcome. Instagram blocks anonymous readers
   often enough that "found nothing" must fall through to ordinary discovery
   rather than degrade the row.
"""
import pytest

import bio_link_service as bl
import search_options as so
import verification_pipeline as vp
import verification_service as vs


# ── unwrapping the platforms' link redirectors ──────────────────────────────

@pytest.mark.parametrize("wrapped,expected", [
    ("https://l.instagram.com/?u=https%3A%2F%2Fwww.youtube.com%2F%40someone&e=x",
     "https://www.youtube.com/@someone"),
    ("https://www.youtube.com/redirect?q=https%3A%2F%2Fx.com%2Fsomeone",
     "https://x.com/someone"),
    ("https://l.facebook.com/l.php?u=https%3A%2F%2Fwww.tiktok.com%2F%40someone",
     "https://www.tiktok.com/@someone"),
    ("https://www.youtube.com/@someone", "https://www.youtube.com/@someone"),
])
def test_redirect_wrappers_resolve_to_the_real_target(wrapped, expected):
    """Un-unwrapped, every one of these classifies as the wrong platform."""
    assert bl.unwrap_redirect(wrapped) == expected


# ── extraction from a profile page ──────────────────────────────────────────

IG_PAGE = """
<html><head>
<meta property="og:title" content="Kako (@kako) • Instagram">
</head><body>
<a href="https://l.instagram.com/?u=https%3A%2F%2Fwww.youtube.com%2F%40kakomusic">yt</a>
<script>{"bio_links":[{"url":"https:\\/\\/www.tiktok.com\\/@kako"}]}</script>
<div>also on twitter.com/kakomusic and www.facebook.com/kako.official</div>
<a href="https://www.instagram.com/explore/tags/music">tags</a>
</body></html>
"""


def test_harvests_every_platform_from_one_page():
    found = bl.links_by_platform(IG_PAGE, exclude_platform="Instagram")
    assert found == {
        "YouTube": "https://www.youtube.com/@kakomusic",
        "TikTok": "https://www.tiktok.com/@kako",
        "X": "https://x.com/kakomusic",
        "Facebook": "https://www.facebook.com/kako.official",
    }


def test_the_anchors_own_platform_is_excluded():
    """An Instagram page is full of Instagram links; none of them are findings."""
    assert "Instagram" not in bl.links_by_platform(IG_PAGE, exclude_platform="Instagram")


def test_non_profile_urls_are_not_adopted():
    page = ('<a href="https://www.instagram.com/p/ABC123">post</a>'
            '<a href="https://www.youtube.com/watch?v=abc">video</a>'
            '<a href="https://x.com/home">home</a>')
    assert bl.links_by_platform(page, exclude_platform="Instagram") == {}


def test_an_empty_or_blocked_page_yields_nothing_rather_than_raising():
    for page in ("", "<html><body>Login required</body></html>"):
        assert bl.links_by_platform(page, exclude_platform="Instagram") == {}


# ── choosing the anchor ─────────────────────────────────────────────────────

def test_instagram_is_preferred_as_the_anchor():
    assert bl.pick_anchor({
        "Instagram": "https://www.instagram.com/kako",
        "YouTube": "https://www.youtube.com/@kako",
    }) == ("Instagram", "https://www.instagram.com/kako")


def test_youtube_anchors_when_instagram_is_absent():
    platform, _ = bl.pick_anchor({"YouTube": "https://www.youtube.com/@kako"})
    assert platform == "YouTube"


def test_no_usable_handle_means_no_anchor():
    for handles in ({}, {"Instagram": ""}, {"Instagram": "https://www.instagram.com/p/A"},
                    {"Facebook": "https://www.facebook.com/kako"}):
        assert bl.pick_anchor(handles) == ("", "")


# ── the pipeline phase ──────────────────────────────────────────────────────

HANDLES = {"Instagram": "https://www.instagram.com/kako"}


def _stub_harvest(monkeypatch, found, calls=None):
    def harvest(anchor_url, anchor_platform):
        if calls is not None:
            calls.append((anchor_platform, anchor_url))
        return dict(found)
    monkeypatch.setattr(bl, "harvest", harvest)


def test_wikipedia_mode_never_harvests(monkeypatch):
    """The tuned flow must be untouched — no fetch, no adoption, no saving."""
    calls = []
    _stub_harvest(monkeypatch, {"YouTube": "https://www.youtube.com/@kako"}, calls)
    assert vp._row_bio_link_phase("Kako", HANDLES, {}, so.DEFAULT) == {}
    assert calls == []


CUSTOM = so.SearchOptions(mode="custom", query_template="{name} site:{domain}")


def test_custom_mode_adopts_the_links_and_the_anchor(monkeypatch):
    _stub_harvest(monkeypatch, {"YouTube": "https://www.youtube.com/@kako",
                                "X": "https://x.com/kako"})
    out = vp._row_bio_link_phase("Kako", HANDLES, {}, CUSTOM)
    assert set(out) == {"Instagram", "YouTube", "X"}
    assert all(r.status == vs.STATUS_VERIFIED and r.confidence == 100 for r in out.values())


def test_the_reason_names_the_anchor_so_the_assumption_stays_auditable(monkeypatch):
    """These cells skip adjudication; the file must say why they were trusted."""
    _stub_harvest(monkeypatch, {"YouTube": "https://www.youtube.com/@kako"})
    reason = vp._row_bio_link_phase("Kako", HANDLES, {}, CUSTOM)["YouTube"].reason
    assert "instagram.com/kako" in reason and "input file" in reason


def test_an_analyst_rejection_outranks_the_bio(monkeypatch):
    _stub_harvest(monkeypatch, {"YouTube": "https://www.youtube.com/@kako"})
    decisions = {"rejected": {"YouTube": "https://www.youtube.com/@kako"}}
    assert "YouTube" not in vp._row_bio_link_phase("Kako", HANDLES, decisions, CUSTOM)


def test_no_anchor_means_the_row_runs_the_normal_pipeline(monkeypatch):
    calls = []
    _stub_harvest(monkeypatch, {"YouTube": "https://www.youtube.com/@kako"}, calls)
    assert vp._row_bio_link_phase("Kako", {}, {}, CUSTOM) == {}
    assert calls == []


def test_a_blocked_harvest_still_confirms_the_client_handle(monkeypatch):
    """Instagram often shows an anonymous reader nothing. That is not a failure."""
    _stub_harvest(monkeypatch, {})
    out = vp._row_bio_link_phase("Kako", HANDLES, {}, CUSTOM)
    assert set(out) == {"Instagram"}


def test_a_harvest_that_raises_degrades_to_the_normal_pipeline(monkeypatch):
    """A network failure must cost the four discovered cells, nothing more."""
    def boom(anchor_url, anchor_platform):
        raise RuntimeError("network on fire")
    monkeypatch.setattr(bl, "harvest", boom)
    out = vp._row_bio_link_phase("Kako", HANDLES, {}, CUSTOM)
    # The client's own handle still stands on the file's authority; every other
    # platform falls through to ordinary discovery.
    assert set(out) == {"Instagram"}


def test_a_second_anchor_is_tried_when_the_first_publishes_nothing(monkeypatch):
    """Instagram routinely shows an anonymous reader no links. YouTube does."""
    tried = []

    def harvest(anchor_url, anchor_platform):
        tried.append(anchor_platform)
        return {} if anchor_platform == "Instagram" else {"X": "https://x.com/kako"}

    monkeypatch.setattr(bl, "harvest", harvest)
    out = vp._row_bio_link_phase("Kako", {
        "Instagram": "https://www.instagram.com/kako",
        "YouTube": "https://www.youtube.com/@kako",
    }, {}, CUSTOM)
    assert tried == ["Instagram", "YouTube"]
    assert out["X"].status == vs.STATUS_VERIFIED
    assert "youtube.com/@kako" in out["X"].reason


def test_platform_chrome_is_never_adopted_as_a_profile():
    """Instagram's own markup contains facebook.com/ig_xsite_user_info."""
    page = '<a href="https://www.facebook.com/ig_xsite_user_info">x</a>'
    assert bl.links_by_platform(page, exclude_platform="Instagram") == {}


# ── phase 1 must not re-search what phase 0 settled ─────────────────────────

def test_adopted_platforms_are_never_searched(monkeypatch):
    """The whole saving is the skipped work — assert it is actually skipped."""
    searched = []

    def _resolve(talent, platform, wiki_meta, **kw):
        searched.append(platform)
        return vs.VerificationResult(platform=platform, status=vs.STATUS_NOT_FOUND)

    monkeypatch.setattr(vp, "_resolve_platform_serper", _resolve)
    resolved = {"Instagram": vs.VerificationResult(
        platform="Instagram", best_candidate="https://www.instagram.com/kako",
        status=vs.STATUS_VERIFIED, confidence=100)}

    out = vp._row_serper_phase("Kako", _FakeMeta(), resolved=resolved, options=CUSTOM)

    assert "Instagram" not in searched
    assert sorted(searched) == sorted(p for p in vp.PLATFORMS if p != "Instagram")
    assert out["Instagram"].status == vs.STATUS_VERIFIED


class _FakeMeta:
    name = "Kako"
    talent = "Kako"
    is_person = True
    official_website = ""

    def to_prompt_dict(self):
        return {"name": "Kako"}
