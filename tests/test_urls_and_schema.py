"""URL handling and the workbook schema — the layers everything else sits on."""
import pandas as pd
import pytest

import excel_service as ex
import social_urls as su


# ── profile URL validation ──────────────────────────────────────────────────

@pytest.mark.parametrize("url,platform", [
    ("https://www.instagram.com/someone", "Instagram"),
    ("https://www.facebook.com/someone", "Facebook"),
    ("https://www.youtube.com/@someone", "YouTube"),
    ("https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv", "YouTube"),
    ("https://www.tiktok.com/@someone", "TikTok"),
    ("https://x.com/someone", "X"),
    ("https://twitter.com/someone", "X"),
])
def test_accepts_real_profile_urls(url, platform):
    assert su.is_valid_profile_url(url, platform)


@pytest.mark.parametrize("url,platform", [
    ("https://www.instagram.com/p/ABC123", "Instagram"),        # a post
    ("https://www.instagram.com/reel/ABC", "Instagram"),
    ("https://www.youtube.com/watch?v=abc", "YouTube"),         # a video
    ("https://x.com/someone/status/123", "X"),                  # a tweet
    ("https://x.com/home", "X"),                                # a site route
    ("https://x.com/search", "X"),
    ("https://www.tiktok.com/@someone/video/123", "TikTok"),
    ("https://www.facebook.com/someone/posts/123", "Facebook"),
    ("ftp://www.instagram.com/someone", "Instagram"),           # wrong scheme
])
def test_rejects_non_profile_urls(url, platform):
    assert not su.is_valid_profile_url(url, platform)


# ── coercion of messy spreadsheet cells ─────────────────────────────────────

@pytest.mark.parametrize("value,platform,expected", [
    ("trevorstj", "Instagram", "https://www.instagram.com/trevorstj"),
    ("@ScotTeller21", "X", "https://x.com/ScotTeller21"),
    ("http://twitter.com/trevorstjohn", "X", "https://x.com/trevorstjohn"),
    ("FALSE|http://www.facebook.com/a_b", "Facebook", "https://www.facebook.com/a_b"),
    ("UCabcdefghijklmnopqrstuv", "YouTube",
     "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv"),
])
def test_coerces_handles_and_urls(value, platform, expected):
    assert su.coerce_profile_url(value, platform) == expected


@pytest.mark.parametrize("value", ["", "nan", "none", "null", "n/a", "-", None])
def test_rejects_nullish_cells(value):
    """A pandas NaN stringifies to 'nan' and once became the handle @nan."""
    assert su.coerce_profile_url(value, "Instagram") == ""


def test_rejects_url_pointing_at_a_different_platform():
    assert su.coerce_profile_url("https://www.instagram.com/x", "Facebook") == ""


# ── normalisation ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://twitter.com/ErikPalladino",
    "https://www.twitter.com/ErikPalladino",
    "https://mobile.twitter.com/ErikPalladino",
    "https://x.com/ErikPalladino",
])
def test_twitter_and_x_normalise_together(url):
    """Without this, the same account from two sources never dedupes."""
    assert su.normalize_profile_url(url, "X") == "https://x.com/ErikPalladino"


def test_trailing_slash_is_stripped():
    assert (su.normalize_profile_url("https://www.instagram.com/a/", "Instagram")
            == "https://www.instagram.com/a")


# ── workbook schema ─────────────────────────────────────────────────────────

def test_ordered_columns_cover_every_platform():
    cols = ex.ordered_columns()
    for p in su.PLATFORMS:
        assert p in cols
        for suffix in ("Status", "Confidence", "Reason"):
            assert f"{p} {suffix}" in cols


def test_working_columns_are_not_exported(tmp_path):
    """_input_metadata / _input_handles must never reach the client's file."""
    df = ex.build_talent_df(["Someone"])
    out = ex.save_results(df, output_dir=tmp_path)
    assert not [c for c in pd.read_excel(out).columns if str(c).startswith("_")]


def test_reads_handle_columns_and_strips_client_tag(tmp_path):
    src = tmp_path / "in.xlsx"
    pd.DataFrame([{
        "title": "Someone - DAR", "title_category": "Talent",
        "instagram_user": "someone_", "twitter_handle": "http://twitter.com/someone",
    }]).to_excel(src, index=False)
    df = ex.load_talent_table_from_path(src)
    assert df.iloc[0][ex.TALENT_COL] == "Someone"          # "- DAR" stripped
    handles = df.iloc[0][ex.INPUT_HANDLES_COL]
    assert handles["Instagram"] == "https://www.instagram.com/someone_"
    assert handles["X"] == "https://x.com/someone"
