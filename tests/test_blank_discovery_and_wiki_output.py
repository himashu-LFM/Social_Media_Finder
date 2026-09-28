"""
Tester-confirmed changes, items 3 and 4.

Item 3 — the Wikipedia page the tool resolved as ground truth is surfaced in the
         compact export (URL only), so an analyst can validate against it.
Item 4 — every blank platform is searched, including blank Instagram/YouTube;
         nothing is skipped just because the input cell was empty, and there is
         no per-platform priority. (This regressed silently once, masked by an
         exhausted Serper key, so it is pinned down here.)
"""
import pandas as pd

from app.output import excel as ex
from app.pipeline import orchestrator as vp
from app.platforms import social_urls as su


# ── Item 3: Wikipedia URL in the compact output ──────────────────────────────

def test_compact_sheet_has_a_wikipedia_url_column_right_after_name():
    cols = ex.compact_columns()
    assert cols[:3] == ["brand_id", "name", ex.WIKI_COL]


def test_compact_export_writes_the_resolved_wikipedia_url(tmp_path):
    df = ex.build_talent_df(["Jane Doe"])
    # What the pipeline writes back onto the row: the page it used as ground truth.
    df.at[df.index[0], ex.WIKI_COL] = "https://en.wikipedia.org/wiki/Jane_Doe"

    out = ex.save_compact_report(df, output_dir=tmp_path)
    got = pd.read_excel(out)

    assert ex.WIKI_COL in got.columns
    assert got.iloc[0][ex.WIKI_COL] == "https://en.wikipedia.org/wiki/Jane_Doe"


def test_compact_export_leaves_wikipedia_blank_when_there_is_none(tmp_path):
    df = ex.build_talent_df(["No Wiki Person"])  # WIKI_COL defaults to ""
    out = ex.save_compact_report(df, output_dir=tmp_path)
    got = pd.read_excel(out).fillna("")
    assert got.iloc[0][ex.WIKI_COL] == ""


def test_assemble_row_out_carries_the_wikipedia_url():
    out = vp._assemble_row_out({}, "https://en.wikipedia.org/wiki/X")
    assert out[ex.WIKI_COL] == "https://en.wikipedia.org/wiki/X"
    # and defaults to blank when the row had no Wikipedia page
    assert vp._assemble_row_out({})[ex.WIKI_COL] == ""


# ── Item 4: every blank platform is searched ─────────────────────────────────

def test_blank_cells_on_a_no_wiki_row_are_all_searched(monkeypatch):
    """A names-only row (no handles, no Wikipedia) must fan out to all 5 platforms."""
    from app.discovery import serper as serper_service
    from app.discovery import bio_links as bio_link_service

    searched: list = []

    def fake_discover(talent, platform, *args, **kwargs):
        searched.append(platform)
        return []

    monkeypatch.setattr(serper_service, "is_configured", lambda: True)
    monkeypatch.setattr(serper_service, "discover_by_site", fake_discover)
    # Keep Phase 0 offline: no client handles means no anchor to read anyway.
    monkeypatch.setattr(bio_link_service, "anchors", lambda handles: [])

    df = ex.build_talent_df(["Some Person"])
    vp.run_pipeline_on_dataframe(df)

    # Blank Instagram and YouTube are searched like every other platform.
    assert set(searched) == set(su.PLATFORMS)
