"""
Tester-confirmed change, item 2 — the YouTube "|" distributor rule.

A YouTube input value written as "<channel>|<title>" (e.g.
"http://www.youtube.com/@sonypictures|Spider-Man: Brand New Day") is a
distributor/label channel shared across many titles, NOT this title's own. It is
never trusted or scraped: YouTube is sent to discovery for the title's own
channel, and the distributor URL is only surfaced — flagged Manual Review — when
no title-specific channel is found.
"""
import pandas as pd

from app.output import excel as ex
from app.pipeline import orchestrator as vp
from app.verification import verifier as vs


# ── input parsing ────────────────────────────────────────────────────────────

def test_piped_youtube_is_captured_as_distributor_not_trusted(tmp_path):
    src = tmp_path / "in.xlsx"
    pd.DataFrame([{
        "title": "Spider-Man: Brand New Day",
        "youtube_channel_username":
            "http://www.youtube.com/@sonypictures|Spider-Man: Brand New Day",
    }]).to_excel(src, index=False)
    row = ex.load_talent_table_from_path(src).iloc[0]

    assert "YouTube" not in row[ex.INPUT_HANDLES_COL]     # never trusted
    assert "sonypictures" in row[ex.YT_DISTRIBUTOR_COL]   # kept as flagged fallback


def test_plain_youtube_handle_is_still_trusted(tmp_path):
    src = tmp_path / "in.xlsx"
    pd.DataFrame([{
        "title": "Billy Gardell",
        "youtube_channel_username": "https://www.youtube.com/@BillyGardellOfficial",
    }]).to_excel(src, index=False)
    row = ex.load_talent_table_from_path(src).iloc[0]

    assert row[ex.INPUT_HANDLES_COL]["YouTube"].endswith("BillyGardellOfficial")
    assert row[ex.YT_DISTRIBUTOR_COL] == ""


def test_the_distributor_column_never_reaches_the_export(tmp_path):
    src = tmp_path / "in.xlsx"
    pd.DataFrame([{
        "title": "Spider-Man: Brand New Day",
        "youtube_channel_username": "http://www.youtube.com/@sonypictures|Spider-Man",
    }]).to_excel(src, index=False)
    df = ex.load_talent_table_from_path(src)
    out = ex.save_results(df, output_dir=tmp_path)
    assert not [c for c in pd.read_excel(out).columns if str(c).startswith("_")]


# ── the flagged-fallback override ────────────────────────────────────────────

def test_distributor_is_flagged_when_no_title_channel_is_found():
    res = vp._apply_yt_distributor_override(
        {"YouTube": vs.VerificationResult(platform="YouTube", status=vs.STATUS_NOT_FOUND)},
        "https://www.youtube.com/@sonypictures")
    yt = res["YouTube"]
    assert yt.status == vs.STATUS_MANUAL
    assert yt.best_candidate == "https://www.youtube.com/@sonypictures"
    assert "distributor" in yt.reason.lower()


def test_a_discovered_title_channel_is_kept_over_the_distributor():
    found = vs.VerificationResult(
        platform="YouTube", best_candidate="https://www.youtube.com/@SpiderManMovie",
        status=vs.STATUS_VERIFIED, confidence=95)
    res = vp._apply_yt_distributor_override(
        {"YouTube": found}, "https://www.youtube.com/@sonypictures")
    assert res["YouTube"].best_candidate.endswith("SpiderManMovie")
    assert res["YouTube"].status == vs.STATUS_VERIFIED


def test_no_distributor_is_a_noop():
    orig = {"YouTube": vs.VerificationResult(platform="YouTube", status=vs.STATUS_NOT_FOUND)}
    assert vp._apply_yt_distributor_override(orig, "") is orig


# ── end to end ───────────────────────────────────────────────────────────────

def test_piped_youtube_end_to_end_flags_the_distributor(monkeypatch, tmp_path):
    monkeypatch.setattr(vp.serper_service, "is_configured", lambda: True)
    monkeypatch.setattr(vp.serper_service, "discover_by_site", lambda *a, **k: [])
    monkeypatch.setattr(vp, "_enrich_candidates", lambda cands, platform: None)
    monkeypatch.setattr(vp.bio_link_service, "harvest", lambda url, platform: {})
    monkeypatch.setattr(vp.apify_service, "instagram_configured", lambda: False)

    src = tmp_path / "in.xlsx"
    pd.DataFrame([{
        "title": "Spider-Man: Brand New Day", "wikipedia_url": "",
        "youtube_channel_username":
            "http://www.youtube.com/@sonypictures|Spider-Man: Brand New Day",
    }]).to_excel(src, index=False)
    df = ex.load_talent_table_from_path(src)

    r = vp.run_pipeline_on_dataframe(df).iloc[0]
    assert r[ex.status_col("YouTube")] == vs.STATUS_MANUAL
    assert "sonypictures" in r[ex.link_col("YouTube")]
