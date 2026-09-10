"""
The brand-report output writer: fill the client's own file in place.

Guards:
  * Original columns/values are preserved verbatim (incl. *_verified columns).
  * A BLANK platform cell is filled with a SURE (Verified) link, as a full URL.
  * A cell the client already filled is never overwritten.
  * Uncertain (Manual Review) links go to the two appended columns, not the
    platform columns — and the reason carries the candidate link(s).
"""
import pandas as pd
from openpyxl import Workbook

from app.output import excel as ex
from app.verification.verifier import STATUS_VERIFIED, STATUS_MANUAL


def _write_brand_report(path):
    wb = Workbook(); ws = wb.active
    ws.append(["title", "instagram_user", "facebook_page", "facebook_verified",
               "twitter_handle", "youtube_channel_username", "tiktok_user"])
    # Row A: everything except TikTok (blank -> will be filled Verified)
    ws.append(["ATP Tour - DAR", "atptour", "http://www.facebook.com/ATPTour",
               "TRUE|http://www.facebook.com/ATPTour", "http://twitter.com/atptour",
               "http://www.youtube.com/@ATPTour", ""])
    # Row B: missing YouTube (blank -> Manual Review) and TikTok
    ws.append(["Cameron Norrie - DAR", "norriee", "http://www.facebook.com/CamNorrie",
               "FALSE|http://www.facebook.com/CamNorrie", "http://twitter.com/cam_norrie",
               "", ""])
    wb.save(path)


def test_brand_report_fill_in_place(tmp_path):
    src = tmp_path / "brand.xlsx"
    _write_brand_report(src)

    df = ex.load_talent_table_from_path(src)
    assert ex._has_source_rows(df)                      # original row captured

    # Simulate pipeline output:
    # Row A -> TikTok Verified (blank cell should be filled, full URL)
    df.at[0, ex.status_col("TikTok")] = STATUS_VERIFIED
    df.at[0, ex.link_col("TikTok")] = "https://www.tiktok.com/@atptour"
    # Row B -> YouTube Manual Review with two candidates (goes to review columns)
    df.at[1, ex.status_col("YouTube")] = STATUS_MANUAL
    df.at[1, ex.link_col("YouTube")] = "https://www.youtube.com/@camnorrie"
    df.at[1, ex.reason_col("YouTube")] = ("SerpApi cited multiple candidates — review: "
                                          "https://youtube.com/@camnorrie  |  https://youtube.com/@cnorrie")

    out_path = ex.save_brand_report(df, output_dir=tmp_path)
    out = pd.read_excel(out_path, dtype=str).fillna("")

    # original columns preserved + 2 new columns appended
    assert list(out.columns)[:7] == ["title", "instagram_user", "facebook_page",
        "facebook_verified", "twitter_handle", "youtube_channel_username", "tiktok_user"]
    assert list(out.columns)[-2:] == [ex.MANUAL_REVIEW_COL, ex.MANUAL_REASON_COL]

    a, b = out.iloc[0], out.iloc[1]

    # untouched: verified column + client-provided handle
    assert a["facebook_verified"] == "TRUE|http://www.facebook.com/ATPTour"
    assert a["instagram_user"] == "atptour"
    assert a["title"] == "ATP Tour - DAR"                # original name kept (incl. -DAR)

    # sure link filled into the blank TikTok cell, as a full URL
    assert a["tiktok_user"].startswith("https://") and "atptour" in a["tiktok_user"]
    assert a[ex.MANUAL_REVIEW_COL] == ""                 # nothing uncertain for row A

    # row B: YouTube stays BLANK in its column; goes to review columns instead
    assert b["youtube_channel_username"] == ""
    assert "YouTube" in b[ex.MANUAL_REVIEW_COL]
    assert "camnorrie" in b[ex.MANUAL_REVIEW_COL]
    assert "cnorrie" in b[ex.MANUAL_REASON_COL]          # both candidates in the reason


def test_save_output_routes_to_brand_report_when_source_present(tmp_path):
    src = tmp_path / "brand.xlsx"
    _write_brand_report(src)
    df = ex.load_talent_table_from_path(src)
    out = ex.save_output(df, output_dir=tmp_path)
    cols = list(pd.read_excel(out, dtype=str).columns)
    assert "title" in cols and ex.MANUAL_REVIEW_COL in cols   # brand-report shape, not tool schema
