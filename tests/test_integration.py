"""Integration tests: full discover_talent flow and the testing.process_row
DataFrame contract that the API + frontend depend on.

Network (Serper, OpenAI, Wikipedia/Wikidata) is fully mocked.

Run:
    cd Social_Media_Finder
    python -m pytest tests/ -v
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import profile_discovery as pd_mod  # noqa: E402
import testing  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
#  discover_talent — Steps 1-7 across all platforms
# ─────────────────────────────────────────────────────────────────────────────

def test_discover_talent_mixes_wikipedia_and_llm(monkeypatch):
    # Step 1 metadata
    monkeypatch.setattr(pd_mod, "extract_wikipedia_metadata",
                        lambda url, talent="": {"name": talent, "occupation": "journalist"})
    # Step 2: Wikipedia already has X + Facebook
    monkeypatch.setattr(pd_mod, "get_wikipedia_socials", lambda *a, **k: {
        "X": "https://x.com/AriMelber",
        "Facebook": "https://www.facebook.com/AriMelber",
    })
    # Step 3: Serper returns one IG profile, junk for the rest
    monkeypatch.setattr(pd_mod, "serper_search_with_retry", lambda query, **k: (
        [{"title": "", "snippet": "", "link": "https://www.instagram.com/arimelber"}]
        if "Instagram" in query else
        [{"title": "", "snippet": "", "link": "https://www.nytimes.com/x"}]
    ))
    # Step 5: LLM confidently accepts the IG profile
    monkeypatch.setattr(pd_mod, "llm_verify", lambda meta, platform, cands, **k: (
        {"selected_url": cands[0], "confidence": 0.9, "reasoning": "ok"}
        if cands else {"selected_url": "", "confidence": 0.0, "reasoning": "none"}
    ))

    seen = []
    out = pd_mod.discover_talent(
        "Ari Melber",
        wikipedia_url="https://en.wikipedia.org/wiki/Ari_Melber",
        threshold=0.75,
        on_platform=lambda p, phase: seen.append((p, phase)),
    )

    assert out["X"]["source"] == pd_mod.SOURCE_WIKIPEDIA
    assert out["Facebook"]["source"] == pd_mod.SOURCE_WIKIPEDIA
    assert out["Instagram"]["source"] == pd_mod.SOURCE_LLM
    assert out["Instagram"]["profile_url"] == "https://www.instagram.com/arimelber"
    assert out["TikTok"]["source"] == pd_mod.SOURCE_NOT_FOUND
    assert out["YouTube"]["source"] == pd_mod.SOURCE_NOT_FOUND
    # Progress callback fired start+done for all 5 platforms.
    assert seen.count(("Instagram", "start")) == 1
    assert seen.count(("Instagram", "done")) == 1

    # Every result carries the Step-7 output shape.
    for platform, res in out.items():
        assert set(res) >= {"talent_name", "platform", "profile_url", "source", "confidence"}
        assert res["platform"] == platform


# ─────────────────────────────────────────────────────────────────────────────
#  testing.process_row — wide-Excel column contract (API/frontend depend on it)
# ─────────────────────────────────────────────────────────────────────────────

def test_process_row_writes_expected_columns(monkeypatch):
    monkeypatch.setattr(testing.profile_discovery, "extract_wikipedia_metadata",
                        lambda url, talent="": {"name": talent})
    monkeypatch.setattr(testing.profile_discovery, "get_wikipedia_socials",
                        lambda *a, **k: {"X": "https://x.com/AriMelber"})

    def fake_discover(talent, platform, metadata, wiki_url=None, threshold=0.75):
        base = {"talent_name": talent, "platform": platform}
        if wiki_url:
            return {**base, "profile_url": wiki_url, "source": pd_mod.SOURCE_WIKIPEDIA,
                    "confidence": 0.97, "reasoning": "wiki"}
        if platform == "Instagram":
            return {**base, "profile_url": "https://www.instagram.com/arimelber",
                    "source": pd_mod.SOURCE_LLM, "confidence": 0.9, "reasoning": "llm"}
        return {**base, "profile_url": "", "source": pd_mod.SOURCE_NOT_FOUND,
                "confidence": 0.0, "reasoning": "none"}

    monkeypatch.setattr(testing.profile_discovery, "discover_platform", fake_discover)
    monkeypatch.setattr(testing.time, "sleep", lambda *_a, **_k: None)

    df = testing.load_talent_table_from_path  # noqa: F841  (ensure import works)
    df = testing._ensure_pipeline_columns(pd.DataFrame({
        "Talent Name": ["Ari Melber"],
        "Wikipedia URL": ["https://en.wikipedia.org/wiki/Ari_Melber"],
    }))
    testing.ROW_PLATFORM_CONFIDENCE.clear()
    testing.ROW_PLATFORM_SOURCE.clear()

    testing.process_row(df, df.index[0])

    row = df.iloc[0]
    # Links written into the existing platform columns
    assert row["X"] == "https://x.com/AriMelber"
    assert row["Instagram"] == "https://www.instagram.com/arimelber"
    assert row["TikTok"] == "" and row["YouTube"] == "" and row["Facebook"] == ""
    # Per-platform confidence columns populated for found, blank/NaN for not found
    assert float(row["X Confidence"]) == 0.97
    assert float(row["Instagram Confidence"]) == 0.9
    assert pd.isna(row["TikTok Confidence"])
    # Aggregate Confidence = mean of found platform confidences
    assert abs(float(row["Confidence"]) - (0.97 + 0.9) / 2) < 1e-6
    # Source cell summarises provenance and is backward-compatible free text
    assert "X:WIKIPEDIA" in row["Source"]
    assert "Instagram:LLM_VERIFIED" in row["Source"]


def test_process_row_honours_prefilled_input(monkeypatch):
    monkeypatch.setattr(testing.profile_discovery, "extract_wikipedia_metadata",
                        lambda url, talent="": {"name": talent})
    monkeypatch.setattr(testing.profile_discovery, "get_wikipedia_socials",
                        lambda *a, **k: {})
    # discover_platform must NOT be called for a pre-filled platform.
    monkeypatch.setattr(testing.profile_discovery, "discover_platform",
                        lambda *a, **k: {"profile_url": "", "source": pd_mod.SOURCE_NOT_FOUND,
                                         "confidence": 0.0, "reasoning": "x"})
    monkeypatch.setattr(testing.time, "sleep", lambda *_a, **_k: None)

    df = testing._ensure_pipeline_columns(pd.DataFrame({
        "Talent Name": ["Ari Melber"],
        "Instagram URL": ["https://www.instagram.com/arimelber"],
    }))
    testing.ROW_PLATFORM_CONFIDENCE.clear()
    testing.ROW_PLATFORM_SOURCE.clear()

    testing.process_row(df, df.index[0])
    row = df.iloc[0]
    assert row["Instagram"] == "https://www.instagram.com/arimelber"
    assert float(row["Instagram Confidence"]) == 1.0
    assert "Instagram:INPUT" in row["Source"]
