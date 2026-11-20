"""
PR #28 review point 1: a client-supplied PRIVATE Instagram that _validate_input_handle
sends to Manual must stay Manual after _corroborate_row, whether or not the
logged-out page fetch exposes "is_private":true (LFAI-153 gap #2).
Expected on 60a4918: 'flag_in_html' PASSES, 'flag_missing' FAILS (comes back verified).
"""
import pytest

from app.discovery import apify as apify_service
from app.identity import wikipedia as wiki
from app.output import profile_metadata as pm
# Captured at import time, before conftest's autouse fixture stubs it out.
from app.output.profile_metadata import fetch_profile_metadata as _REAL_FETCH
from app.pipeline import orchestrator as vp
from app.pipeline import options as so

IG_URL = "https://www.instagram.com/janedoe"
YT_URL = "https://www.youtube.com/@janedoe"

_HEAD = ('<html><head><title>Jane Doe (&#064;janedoe) • Instagram photos and videos</title>'
         '<meta property="og:title" content="Jane Doe (@janedoe) • Instagram photos and videos">'
         '<meta property="og:description" content="1,234 Followers, 210 Following, 0 Posts">'
         '</head><body>')
HTML_WITH_FLAG = _HEAD + '<script>{"is_private":true}</script></body></html>'
HTML_NO_FLAG = _HEAD + '</body></html>'   # logged-out fetch that doesn't expose is_private


@pytest.mark.parametrize("page_html", [HTML_WITH_FLAG, HTML_NO_FLAG],
                         ids=["flag_in_html", "flag_missing"])
def test_private_client_instagram_stays_manual_after_corroboration(monkeypatch, page_html):
    wiki_meta = wiki.WikiMetadata(talent="Jane Doe", name="Jane Doe")

    # 1) Phase 0: Apify reads the client handle and reports it PRIVATE -> Manual.
    monkeypatch.setattr(apify_service, "instagram_configured", lambda: True)
    monkeypatch.setattr(apify_service, "instagram_profile",
                        lambda url: {"display_name": "Jane Doe", "username": "janedoe",
                                     "private": True})
    ig = vp._validate_input_handle("Instagram", IG_URL, wiki_meta, so.DEFAULT, talent="Jane Doe")
    assert ig.status == vp.STATUS_MANUAL            # precondition: private guard fired

    # 2) Corroboration with the REAL enrichment code; only the page HTML differs.
    pm.clear_cache()
    monkeypatch.setattr(pm, "fetch_profile_metadata", _REAL_FETCH)
    monkeypatch.setattr(pm, "_fetch_html", lambda url: page_html)
    monkeypatch.setattr(vp.serper_service, "context_for_url",
                        lambda url, *a, **k: {"serper_title": "Jane Doe (@janedoe) • Instagram"})
    # LLM agrees once it sees the same handle already Verified on YouTube.
    monkeypatch.setattr(
        vp.verification_service, "verify_platform",
        lambda platform, gt, cands, **k: vp.VerificationResult(
            platform=platform, best_candidate=cands[0]["url"], status=vp.STATUS_VERIFIED,
            confidence=92, decision="verified", reason="Handle matches YouTube."))

    results = {
        "Instagram": ig,
        "YouTube": vp.VerificationResult(platform="YouTube", best_candidate=YT_URL,
                                         status=vp.STATUS_VERIFIED, confidence=95,
                                         decision="verified"),
    }
    out = vp._corroborate_row("Jane Doe", wiki_meta, results, so.DEFAULT)

    assert out["Instagram"].status == vp.STATUS_MANUAL, (
        f"private client IG promoted to {out['Instagram'].status!r} by corroboration")
