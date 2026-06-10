"""Unit tests for profile_discovery — Wikipedia + Serper + LLM workflow.

All network calls (Serper, OpenAI, Wikipedia/Wikidata) are mocked, so these
tests run offline and deterministically.

Run:
    cd Social_Media_Finder
    python -m pytest tests/ -v
"""

import sys
from pathlib import Path

import pytest

# Make the package importable when running pytest from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import profile_discovery as pd_mod  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 4 — candidate selection / filtering
# ─────────────────────────────────────────────────────────────────────────────

def _results(*links):
    return [{"title": "", "snippet": "", "link": l} for l in links]


def test_select_candidates_takes_top_n_as_returned():
    # New contract: NO profile-shape filtering. Posts/reels/explore URLs are
    # kept (the LLM decides) — only order + top_n + host correctness matter.
    results = _results(
        "https://www.instagram.com/arimelber",           # keep (1)
        "https://www.instagram.com/p/Cabc123/",           # keep (2) — no longer rejected
        "https://www.instagram.com/reel/Xyz/",            # keep (3)
        "https://www.instagram.com/explore/tags/news/",   # would be (4) but top_n=3
        "https://twitter.com/arimelber",                  # dropped: wrong platform host
        "https://www.nytimes.com/ari-melber",             # dropped: not instagram.com
    )
    out = pd_mod.select_candidates(results, "Instagram", top_n=3)
    assert out == [
        "https://www.instagram.com/arimelber",
        "https://www.instagram.com/p/Cabc123",
        "https://www.instagram.com/reel/Xyz",
    ]


def test_select_candidates_preserves_query_strings():
    # Profile URLs with query strings (e.g. ?hl=en) must NOT be discarded.
    out = pd_mod.select_candidates(
        _results("https://www.instagram.com/arimelber?hl=en"), "Instagram")
    assert out == ["https://www.instagram.com/arimelber?hl=en"]


def test_select_candidates_dedupes_and_caps_top_n():
    results = _results(
        "https://instagram.com/a",
        "https://www.instagram.com/a/",   # NOTE: www. makes this a distinct host string
        "https://instagram.com/b",
        "https://instagram.com/c",
        "https://instagram.com/d",
    )
    out = pd_mod.select_candidates(results, "Instagram", top_n=3)
    assert len(out) == 3
    assert out[0].endswith("/a")


def test_select_candidates_exact_duplicate_collapses():
    out = pd_mod.select_candidates(
        _results("https://instagram.com/a", "https://instagram.com/a/"), "Instagram")
    assert out == ["https://instagram.com/a"]


def test_select_candidates_all_platforms_host_filtered():
    assert pd_mod.select_candidates(
        _results("https://facebook.com/MSNBC"), "Facebook")[0].endswith("/MSNBC")
    assert pd_mod.select_candidates(
        _results("https://x.com/arimelber"), "X")[0].endswith("/arimelber")
    assert pd_mod.select_candidates(
        _results("https://www.tiktok.com/@arimelber"), "TikTok")[0].endswith("@arimelber")
    assert pd_mod.select_candidates(
        _results("https://www.youtube.com/@MSNBC"), "YouTube")[0].endswith("@MSNBC")
    # Wrong-platform host is dropped even with relaxed filtering.
    assert pd_mod.select_candidates(_results("https://www.nytimes.com/x"), "Instagram") == []


def test_select_candidates_youtube_keeps_non_profile_paths():
    # watch/playlist URLs are NO LONGER rejected — the LLM gets to decide.
    out = pd_mod.select_candidates(
        _results("https://www.youtube.com/watch?v=abc",
                 "https://www.youtube.com/@MSNBC"), "YouTube", top_n=4)
    assert "https://www.youtube.com/watch?v=abc" in out
    assert "https://www.youtube.com/@MSNBC" in out


def test_select_candidates_raw_mode_ignores_host(monkeypatch):
    # RESTRICT_CANDIDATE_HOSTS off -> truly raw top-N (any host).
    monkeypatch.setattr(pd_mod, "RESTRICT_CANDIDATE_HOSTS", False)
    out = pd_mod.select_candidates(
        _results("https://www.nytimes.com/ari-melber",
                 "https://www.instagram.com/arimelber"), "Instagram", top_n=2)
    assert out == [
        "https://www.nytimes.com/ari-melber",
        "https://www.instagram.com/arimelber",
    ]


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 3 — query building + Serper retry
# ─────────────────────────────────────────────────────────────────────────────

def test_build_query_uses_platform_term():
    assert pd_mod.build_query("Ari Melber", "Instagram") == "Ari Melber Instagram"
    assert pd_mod.build_query("Ari Melber", "X") == "Ari Melber X"


def test_serper_retry_then_success(monkeypatch):
    calls = {"n": 0}

    class _Resp:
        status_code = 200

        def json(self):
            return {"organic": [{"title": "t", "snippet": "s", "link": "https://x.com/a"}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 2:
            raise pd_mod.requests.ConnectionError("boom")
        return _Resp()

    monkeypatch.setattr(pd_mod, "SERPER_API_KEY", "test-key")
    monkeypatch.setattr(pd_mod, "RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(pd_mod.requests, "post", fake_post)

    out = pd_mod.serper_search_with_retry("q", max_retries=3)
    assert out and out[0]["link"] == "https://x.com/a"
    assert calls["n"] == 2  # retried once


def test_serper_fatal_error_raises(monkeypatch):
    class _Resp:
        status_code = 401

        def json(self):
            return {"message": "Unauthorized: invalid api key"}

    monkeypatch.setattr(pd_mod, "SERPER_API_KEY", "bad")
    monkeypatch.setattr(pd_mod.requests, "post",
                        lambda *a, **k: _Resp())
    with pytest.raises(RuntimeError):
        pd_mod.serper_search_with_retry("q", max_retries=3)


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 5 — LLM verification (never invents URLs, respects candidates)
# ─────────────────────────────────────────────────────────────────────────────

def _patch_openai(monkeypatch, payload: str):
    monkeypatch.setattr(pd_mod, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(pd_mod, "_call_openai", lambda messages, max_retries=3: payload)


def test_llm_verify_returns_selected(monkeypatch):
    _patch_openai(
        monkeypatch,
        '{"selected_url": "https://x.com/arimelber", "confidence": 0.93, "reasoning": "match"}',
    )
    out = pd_mod.llm_verify({"name": "Ari Melber"}, "X", ["https://x.com/arimelber"])
    assert out["selected_url"] == "https://x.com/arimelber"
    assert out["confidence"] == 0.93


def test_llm_verify_discards_invented_url(monkeypatch):
    # LLM returns a URL that is NOT in the candidate set — must be discarded.
    _patch_openai(
        monkeypatch,
        '{"selected_url": "https://x.com/IMPOSTOR", "confidence": 0.99, "reasoning": "x"}',
    )
    out = pd_mod.llm_verify({"name": "Ari Melber"}, "X", ["https://x.com/arimelber"])
    assert out["selected_url"] == ""
    assert out["confidence"] <= 0.3


def test_llm_verify_handles_not_found(monkeypatch):
    _patch_openai(
        monkeypatch,
        '{"selected_url": "NOT_FOUND", "confidence": 0.2, "reasoning": "uncertain"}',
    )
    out = pd_mod.llm_verify({"name": "x"}, "X", ["https://x.com/a"])
    assert out["selected_url"] == ""


def test_llm_verify_no_candidates_short_circuits(monkeypatch):
    # Should not even call the LLM when there are no candidates.
    monkeypatch.setattr(pd_mod, "_call_openai",
                        lambda *a, **k: pytest.fail("LLM should not be called"))
    out = pd_mod.llm_verify({"name": "x"}, "X", [])
    assert out["selected_url"] == ""


# ─────────────────────────────────────────────────────────────────────────────
#  STEPS 2-7 — discover_platform end to end (mocked)
# ─────────────────────────────────────────────────────────────────────────────

def test_discover_platform_wikipedia_shortcut(monkeypatch):
    # When a Wikipedia social link exists, Serper/LLM must be skipped.
    monkeypatch.setattr(pd_mod, "serper_search_with_retry",
                        lambda *a, **k: pytest.fail("Serper should be skipped"))
    out = pd_mod.discover_platform(
        "Ari Melber", "X", {"name": "Ari Melber"},
        wiki_url="https://x.com/AriMelber",
    )
    assert out["source"] == pd_mod.SOURCE_WIKIPEDIA
    assert out["profile_url"] == "https://x.com/AriMelber"
    assert out["confidence"] == pd_mod.WIKIPEDIA_CONFIDENCE


def test_discover_platform_llm_verified_above_threshold(monkeypatch):
    monkeypatch.setattr(pd_mod, "serper_search_with_retry",
                        lambda *a, **k: _results("https://www.instagram.com/arimelber"))
    monkeypatch.setattr(
        pd_mod, "llm_verify",
        lambda *a, **k: {"selected_url": "https://www.instagram.com/arimelber",
                         "confidence": 0.91, "reasoning": "ok"},
    )
    out = pd_mod.discover_platform("Ari Melber", "Instagram", {"name": "Ari Melber"},
                                   threshold=0.75)
    assert out["source"] == pd_mod.SOURCE_LLM
    assert out["profile_url"] == "https://www.instagram.com/arimelber"
    assert out["confidence"] == 0.91


def test_discover_platform_below_threshold_is_not_found(monkeypatch):
    monkeypatch.setattr(pd_mod, "serper_search_with_retry",
                        lambda *a, **k: _results("https://www.instagram.com/arimelber"))
    monkeypatch.setattr(
        pd_mod, "llm_verify",
        lambda *a, **k: {"selected_url": "https://www.instagram.com/arimelber",
                         "confidence": 0.5, "reasoning": "weak"},
    )
    out = pd_mod.discover_platform("Ari Melber", "Instagram", {"name": "Ari Melber"},
                                   threshold=0.75)
    assert out["source"] == pd_mod.SOURCE_NOT_FOUND
    assert out["profile_url"] == ""


def test_discover_platform_no_candidates_is_not_found(monkeypatch):
    monkeypatch.setattr(pd_mod, "serper_search_with_retry",
                        lambda *a, **k: _results("https://www.nytimes.com/x"))
    monkeypatch.setattr(pd_mod, "llm_verify",
                        lambda *a, **k: pytest.fail("LLM should be skipped"))
    out = pd_mod.discover_platform("Ari Melber", "Instagram", {"name": "Ari Melber"})
    assert out["source"] == pd_mod.SOURCE_NOT_FOUND
    assert out["candidate_urls"] == []


def test_discover_platform_exposes_candidate_urls(monkeypatch):
    # Step 6 output: candidate_urls must carry the top-N list shown to the LLM.
    monkeypatch.setattr(pd_mod, "serper_search_with_retry",
                        lambda *a, **k: _results("https://www.instagram.com/arimelber",
                                                 "https://www.instagram.com/p/Cabc/"))
    monkeypatch.setattr(
        pd_mod, "llm_verify",
        lambda meta, platform, cands, **k: {"selected_url": cands[0],
                                            "confidence": 0.8, "reasoning": "ok"},
    )
    out = pd_mod.discover_platform("Ari Melber", "Instagram", {"name": "Ari Melber"},
                                   threshold=0.5)
    assert out["source"] == pd_mod.SOURCE_LLM
    assert out["candidate_urls"] == [
        "https://www.instagram.com/arimelber",
        "https://www.instagram.com/p/Cabc",
    ]


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 1 — metadata extraction (mocked Wikidata + REST)
# ─────────────────────────────────────────────────────────────────────────────

def test_extract_wikipedia_metadata(monkeypatch):
    entity = {
        "labels": {"en": {"value": "Ari Melber"}},
        "claims": {
            "P106": [{"mainsnak": {"snaktype": "value",
                                   "datavalue": {"value": {"id": "Q1930187"}}}}],   # journalist
            "P27": [{"mainsnak": {"snaktype": "value",
                                  "datavalue": {"value": {"id": "Q30"}}}}],          # USA
            "P569": [{"mainsnak": {"snaktype": "value",
                                   "datavalue": {"value": {"time": "+1980-03-31T00:00:00Z"}}}}],
            "P856": [{"mainsnak": {"snaktype": "value",
                                   "datavalue": {"value": "https://example.com"}}}],
        },
    }
    monkeypatch.setattr(pd_mod.wikidata_lookup, "_wikipedia_url_to_qid", lambda url: "Q123")
    monkeypatch.setattr(pd_mod.wikidata_lookup, "_fetch_wikidata_entity", lambda qid: entity)
    monkeypatch.setattr(pd_mod, "_resolve_entity_labels",
                        lambda qids: {"Q1930187": "journalist", "Q30": "United States"})
    monkeypatch.setattr(pd_mod, "_wikipedia_rest_summary",
                        lambda url: {"title": "Ari Melber",
                                     "description": "American journalist",
                                     "summary": "Ari Naftali Melber is an American attorney..."})

    meta = pd_mod.extract_wikipedia_metadata("https://en.wikipedia.org/wiki/Ari_Melber", "Ari Melber")
    assert meta["name"] == "Ari Melber"
    assert meta["wikidata_qid"] == "Q123"
    assert meta["occupation"] == "journalist"
    assert meta["profession"] == "journalist"
    assert meta["nationality"] == "United States"
    assert meta["birth_date"] == "1980-03-31"
    assert meta["website"] == "https://example.com"
    assert meta["known_for"] == "American journalist"
    assert meta["summary"].startswith("Ari Naftali Melber")


def test_extract_metadata_never_raises_on_failure(monkeypatch):
    monkeypatch.setattr(pd_mod.wikidata_lookup, "_wikipedia_url_to_qid",
                        lambda url: (_ for _ in ()).throw(Exception("network")))
    monkeypatch.setattr(pd_mod.wikidata_lookup, "_name_to_qid", lambda *a, **k: None)
    monkeypatch.setattr(pd_mod, "_wikipedia_rest_summary", lambda url: {})
    meta = pd_mod.extract_wikipedia_metadata("https://en.wikipedia.org/wiki/Foo", "Foo")
    assert meta["name"] == "Foo"
