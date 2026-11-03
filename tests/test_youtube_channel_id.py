"""
YouTube channel-ID normalisation.

Two URL forms of the same channel (…/@handle and …/channel/UC...) must collapse
to one canonical URL so they don't compete as duplicate profiles and scoring
stays consistent. The channel id (UC...) is read from the page; when it can't be
resolved, candidates pass through unchanged.
"""
from app.output import profile_metadata as pm
from app.pipeline import orchestrator as vp

CID = "UC_x5XG1OV2P6uZZ5FSM9Ttw"  # Google Developers (example UC id)
OTHER = "UCBR8-60-B28hp2BkPaoLyYg"


# ── channel-id extraction ────────────────────────────────────────────────────

def test_channel_id_from_inline_json():
    html = f'<script>{{"channelId":"{CID}","title":"x"}}</script>'
    assert pm._youtube_channel_id_from_html(html) == CID


def test_channel_id_from_external_id_json():
    html = f'...{{"externalId":"{CID}"}}...'
    assert pm._youtube_channel_id_from_html(html) == CID


def test_channel_id_from_canonical_path():
    html = f'<link rel="canonical" href="https://www.youtube.com/channel/{CID}">'
    assert pm._youtube_channel_id_from_html(html) == CID


def test_no_channel_id_returns_empty():
    assert pm._youtube_channel_id_from_html("<html>nothing here</html>") == ""
    assert pm._youtube_channel_id_from_html("") == ""


# ── duplicate collapse ───────────────────────────────────────────────────────

def _yt(url, source="serper", **meta):
    return {"url": url, "source": source, "meta": meta}


def test_handle_and_channel_forms_collapse_to_one_canonical():
    cands = [
        _yt("https://www.youtube.com/channel/" + CID, source="wikidata", channel_id=CID),
        _yt("https://www.youtube.com/@dupe", source="serper", channel_id=CID),
    ]
    out = vp._collapse_youtube_duplicates(cands)
    assert len(out) == 1
    assert out[0]["url"] == "https://www.youtube.com/channel/" + CID
    assert out[0]["source"] == "wikidata"  # first candidate's source/priority preserved


def test_handle_only_form_is_rewritten_to_canonical():
    out = vp._collapse_youtube_duplicates([_yt("https://www.youtube.com/@onlyhandle", channel_id=CID)])
    assert out[0]["url"] == "https://www.youtube.com/channel/" + CID


def test_different_channels_are_not_collapsed():
    cands = [
        _yt("https://www.youtube.com/@a", channel_id=CID),
        _yt("https://www.youtube.com/@b", channel_id=OTHER),
    ]
    out = vp._collapse_youtube_duplicates(cands)
    assert len(out) == 2


def test_candidate_without_channel_id_passes_through():
    cand = _yt("https://www.youtube.com/@unresolved")
    out = vp._collapse_youtube_duplicates([cand])
    assert out == [cand]  # unchanged when the id can't be resolved


def test_merge_unions_evidence_from_the_duplicate():
    cands = [
        _yt("https://www.youtube.com/channel/" + CID, source="wikidata", channel_id=CID),
        _yt("https://www.youtube.com/@dupe", channel_id=CID, subscribers="1M"),
    ]
    out = vp._collapse_youtube_duplicates(cands)
    assert out[0]["meta"].get("subscribers") == "1M"  # evidence from the dropped dup kept


# ── integration: collapse actually fires inside _verify, before the LLM ───────

def test_collapse_runs_inside_verify(monkeypatch):
    from app.identity import wikipedia as wiki
    from app.pipeline import options as so

    def fake_enrich(cands, platform):           # enrichment resolves the same id on both forms
        for c in cands:
            c.setdefault("meta", {})["channel_id"] = CID

    monkeypatch.setattr(vp, "_drop_missing_profiles", lambda cands, platform: cands)
    monkeypatch.setattr(vp, "_enrich_candidates", fake_enrich)

    seen = {}

    def fake_verify(platform, gt, cands, **k):
        seen["urls"] = [c["url"] for c in cands]
        return vp.VerificationResult(platform=platform, best_candidate=cands[0]["url"],
                                     status=vp.STATUS_VERIFIED, confidence=95)

    monkeypatch.setattr(vp.verification_service, "verify_platform", fake_verify)

    cands = [
        {"url": "https://www.youtube.com/channel/" + CID, "source": "wikidata", "meta": {}},
        {"url": "https://www.youtube.com/@dupe", "source": "serper", "meta": {}},
    ]
    vp._verify("YouTube", wiki.WikiMetadata(talent="X", name="X"), cands, so.DEFAULT)
    # The LLM saw ONE canonical candidate, not the two competing forms.
    assert seen["urls"] == ["https://www.youtube.com/channel/" + CID]
