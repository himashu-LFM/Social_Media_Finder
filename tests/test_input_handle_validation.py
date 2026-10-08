"""
Input-handle name check (loophole #4).

A client-supplied Instagram/YouTube handle is now confirmed to actually belong to
the talent: the tool reads the profile's own name and lets the LLM compare it to
the name + category. A different-named profile (the classic
"Stephanie Miller -> stephclairesmith" wrong input) is flagged Manual Review
instead of being trusted; an unreadable profile is flagged too; and when the
reader itself is unavailable (Apify off) the input is trusted as before.
"""
from app.discovery import apify as ap
from app.pipeline import options as so
from app.pipeline import orchestrator as vp
from app.verification import verifier as vs

GT = vp._thin_ground_truth("Billy Garvey")


def _res(status, url="https://www.instagram.com/x"):
    return vs.VerificationResult(platform="Instagram", best_candidate=url, status=status)


def test_name_match_stays_verified(monkeypatch):
    monkeypatch.setattr(vp, "_read_input_profile",
                        lambda p, u: ({"display_name": "Billy Garvey"}, True))
    monkeypatch.setattr(vp.verification_service, "verify_platform",
                        lambda *a, **k: _res(vs.STATUS_VERIFIED))
    out = vp._validate_input_handle(
        "Instagram", "https://www.instagram.com/drbillygarvey", GT, so.DEFAULT)
    assert out.status == vs.STATUS_VERIFIED
    assert out.source == "Input file (name-checked)"
    assert out.best_candidate.endswith("drbillygarvey")


def test_name_mismatch_is_flagged_not_trusted(monkeypatch):
    # The real Stephanie Miller case: the file's IG is actually Steph Claire Smith.
    monkeypatch.setattr(vp, "_read_input_profile",
                        lambda p, u: ({"display_name": "Steph Claire Smith"}, True))
    monkeypatch.setattr(vp.verification_service, "verify_platform",
                        lambda *a, **k: _res(vs.STATUS_WRONG))
    gt = vp._thin_ground_truth("Stephanie Miller")
    out = vp._validate_input_handle(
        "Instagram", "https://www.instagram.com/stephclairesmith", gt, so.DEFAULT)
    assert out.status == vs.STATUS_MANUAL
    assert out.source == "Input file (unverified)"
    assert out.best_candidate.endswith("stephclairesmith")   # link is kept
    assert "Steph Claire Smith" in out.reason


def test_unreadable_profile_is_flagged_for_review(monkeypatch):
    monkeypatch.setattr(vp, "_read_input_profile", lambda p, u: (None, True))
    out = vp._validate_input_handle(
        "Instagram", "https://www.instagram.com/private_one", GT, so.DEFAULT)
    assert out.status == vs.STATUS_MANUAL
    assert out.best_candidate.endswith("private_one")


def test_reader_unavailable_keeps_trusting_the_input(monkeypatch):
    # Apify off -> we can't check, so don't flag the whole file; trust it as before.
    monkeypatch.setattr(vp, "_read_input_profile", lambda p, u: (None, False))
    out = vp._validate_input_handle(
        "Instagram", "https://www.instagram.com/someone", GT, so.DEFAULT)
    assert out.status == vs.STATUS_VERIFIED
    assert out.source == "Input file (Phase 0 anchor)"


# ── Scenario 2: the YouTube reader + safety valve (_read_input_profile) ──────

def test_youtube_input_read_returns_the_channel_name(monkeypatch):
    monkeypatch.setattr(vp.profile_metadata, "fetch_profile_metadata",
                        lambda url, platform: {"display_name": "Mamamia"})
    meta, flag = vp._read_input_profile("YouTube", "https://www.youtube.com/@mamamia")
    assert flag is True and meta.get("display_name") == "Mamamia"


def test_youtube_missing_channel_is_flag_worthy(monkeypatch):
    monkeypatch.setattr(vp.profile_metadata, "fetch_profile_metadata", lambda url, platform: {})
    monkeypatch.setattr(vp.profile_metadata, "profile_is_missing", lambda url, platform: True)
    meta, flag = vp._read_input_profile("YouTube", "https://www.youtube.com/@ghost")
    assert meta is None and flag is True          # 404 -> flag for review


def test_youtube_present_but_unreadable_is_trusted(monkeypatch):
    monkeypatch.setattr(vp.profile_metadata, "fetch_profile_metadata", lambda url, platform: {})
    monkeypatch.setattr(vp.profile_metadata, "profile_is_missing", lambda url, platform: False)
    meta, flag = vp._read_input_profile("YouTube", "https://www.youtube.com/@blocked")
    assert meta is None and flag is False         # exists but unreadable -> safety valve: trust


def test_confirmed_handles_excludes_flagged():
    resolved = {
        "Instagram": vs.VerificationResult(
            platform="Instagram", best_candidate="https://ig/ok", status=vs.STATUS_VERIFIED),
        "YouTube": vs.VerificationResult(
            platform="YouTube", best_candidate="https://yt/bad", status=vs.STATUS_MANUAL),
    }
    assert vp._confirmed_handles(resolved) == {"Instagram": "https://ig/ok"}


def _record_anchors(monkeypatch):
    seen = {}

    def rec(handles):
        seen["handles"] = dict(handles)
        return []

    monkeypatch.setattr(vp.bio_link_service, "anchors", rec)
    monkeypatch.setattr(vp.apify_service, "instagram_configured", lambda: False)
    return seen


def test_flagged_instagram_anchor_does_not_seed_the_row(monkeypatch):
    # The supplied Instagram validates as a mismatch (wrong person).
    monkeypatch.setattr(vp, "_validate_input_handle",
                        lambda platform, url, gt, options: vs.VerificationResult(
                            platform=platform, best_candidate=url,
                            status=vs.STATUS_MANUAL, source="Input file (unverified)"))
    seen = _record_anchors(monkeypatch)
    out = vp._row_bio_link_phase(
        "Shakira", {"Instagram": "https://www.instagram.com/wrongperson"}, {}, so.DEFAULT)
    assert out["Instagram"].status == vs.STATUS_MANUAL   # flagged
    assert seen["handles"] == {}                          # NOT used to source the row


def test_confirmed_instagram_anchor_is_used_to_seed_the_row(monkeypatch):
    monkeypatch.setattr(vp, "_validate_input_handle",
                        lambda platform, url, gt, options: vs.VerificationResult(
                            platform=platform, best_candidate=url,
                            status=vs.STATUS_VERIFIED, source="Input file (name-checked)"))
    seen = _record_anchors(monkeypatch)
    ig = "https://www.instagram.com/shakira"
    vp._row_bio_link_phase("Shakira", {"Instagram": ig}, {}, so.DEFAULT)
    assert seen["handles"] == {"Instagram": ig}          # confirmed -> seeds the row


def test_profile_read_and_bio_links_share_one_actor_run(monkeypatch):
    """Reading a profile for validation and for bio-link sourcing costs ONE run."""
    ap.clear_cache()
    calls = {"n": 0}

    def fake_actor(actor_id, actor_input):
        calls["n"] += 1
        return [{"username": "kako", "fullName": "Kako",
                 "biography": "yt: https://youtube.com/@kako"}]

    monkeypatch.setattr(ap, "instagram_configured", lambda: True)
    monkeypatch.setattr(ap, "_run_named_actor", fake_actor)

    url = "https://www.instagram.com/kako"
    prof = ap.instagram_profile(url)
    ap.instagram_bio_links(url)

    assert calls["n"] == 1                      # one actor run, shared via cache
    assert prof.get("display_name") == "Kako"
    ap.clear_cache()
