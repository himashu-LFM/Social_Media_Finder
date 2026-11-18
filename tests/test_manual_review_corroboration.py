"""
Gap #1 — Manual Review reduction via independent corroboration.

Correct FB/X/TikTok handles were being routed to Manual Review purely because
those platforms expose no fetchable content, so the verifier's evidence floor
(`_candidate_has_evidence`) downgraded them. The rescue signals that should clear
that floor — cross-platform agreement and back-links — only ran in custom mode,
and a candidate-dedup bug dropped Serper's evidence when the client handle was
also the independent search hit.

Two levers, both of which promote ONLY on independent corroboration (never the
client's bare assertion):

  A. Cross-platform / back-link corroboration now runs in every mode.
  B. `_add_candidate` merges a duplicate's evidence instead of dropping it, so a
     client handle that Serper independently returned keeps that evidence.

All offline — no Serper, no LLM, no network.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.pipeline import orchestrator as vp          # noqa: E402
from app.pipeline import options as so               # noqa: E402
from app.verification import verifier as vs          # noqa: E402

DEFAULT = so.DEFAULT                                  # Wikipedia mode (is_custom=False)


# ── Lever A: cross-platform agreement, now in default mode ───────────────────
def test_cross_platform_match_tags_in_default_mode():
    # A Manual X handle that equals a handle Verified on Instagram this run is
    # independent evidence — it must be tagged even in the default (non-custom) run.
    cand = {"url": "https://x.com/UCBerkeley", "meta": {}}
    vp._tag_cross_platform_match(cand, "X", {"Instagram": "ucberkeley"}, DEFAULT)
    assert cand["meta"]["anchor_handle_match"] == "ucberkeley"
    # And that tag is exactly what clears the evidence floor that was blocking it.
    assert vs._candidate_has_evidence(cand) is True


def test_cross_platform_match_ignores_same_platform():
    # A handle "confirmed" on the same platform is not cross-platform evidence.
    cand = {"url": "https://x.com/UCBerkeley", "meta": {}}
    vp._tag_cross_platform_match(cand, "X", {"X": "ucberkeley"}, DEFAULT)
    assert "anchor_handle_match" not in cand["meta"]


def test_cross_platform_match_ignores_short_handles():
    # Short handles collide by chance (>= _MIN_ANCHOR_HANDLE_LEN guards this).
    cand = {"url": "https://x.com/uno", "meta": {}}
    vp._tag_cross_platform_match(cand, "X", {"Instagram": "uno"}, DEFAULT)
    assert "anchor_handle_match" not in cand["meta"]


def test_cross_platform_match_ignores_a_different_handle():
    cand = {"url": "https://x.com/someoneelse", "meta": {}}
    vp._tag_cross_platform_match(cand, "X", {"Instagram": "ucberkeley"}, DEFAULT)
    assert "anchor_handle_match" not in cand["meta"]


def test_backlink_tags_in_default_mode():
    cand = {"url": "https://www.youtube.com/@ucberkeley",
            "meta": {"profile_links": {"Instagram": "https://www.instagram.com/ucberkeley"}}}
    vp._tag_backlinks([cand], {"Instagram": "https://www.instagram.com/ucberkeley"}, DEFAULT)
    assert cand["meta"]["backlink_to_client_profile"] == "https://www.instagram.com/ucberkeley"
    assert vs._candidate_has_evidence(cand) is True


def test_wiki_path_enriches_before_tagging_backlinks(monkeypatch):
    """Ordering regression: on the Wikipedia path _resolve_platform_serper must enrich
    candidates (which populates meta['profile_links']) BEFORE _tag_backlinks runs, or the
    back-link is never seen. The candidate starts with NO profile_links — only the (mocked)
    enrichment adds them — so this fails if the tag runs before the enrich."""
    from app.identity import wikipedia as wk              # noqa: E402

    ig = "https://www.instagram.com/ucberkeley"
    monkeypatch.setattr(
        vp, "_serper_primary_candidate",
        lambda *a, **k: ([{"url": "https://www.youtube.com/@ucberkeley",
                           "source": "serper", "meta": {}}], False))

    def fake_enrich(cands, platform):                     # the real fetch would do this
        for c in cands:
            c.setdefault("meta", {})["profile_links"] = {"Instagram": ig}
    monkeypatch.setattr(vp, "_enrich_candidates", fake_enrich)

    captured = {}

    def fake_verify(platform, wiki_meta, candidates, options=so.DEFAULT):
        captured["cands"] = candidates                    # snapshot after tagging
        return vs.VerificationResult(platform=platform, best_candidate=candidates[0]["url"],
                                     status=vs.STATUS_VERIFIED)
    monkeypatch.setattr(vp, "_verify", fake_verify)

    vp._resolve_platform_serper(
        "UC Berkeley", "YouTube",
        wk.WikiMetadata(talent="UC Berkeley", name="UC Berkeley", found=True),
        input_handles_for_backlink={"Instagram": ig})

    assert captured["cands"][0]["meta"].get("backlink_to_client_profile") == ig


# ── Lever B: dedup merges evidence instead of dropping it ─────────────────────
def test_add_candidate_merges_serper_evidence_onto_client_handle():
    cands: list = []
    seen: set = set()
    # 1) client-supplied handle, added first, with no fetched content.
    vp._add_candidate(cands, seen, "X", "https://x.com/UCBerkeley", "input",
                      {"supplied_in_client_record": True})
    assert vs._candidate_has_evidence(cands[0]) is False   # today -> evidence floor -> Manual

    # 2) Serper INDEPENDENTLY returns the same handle, WITH a snippet.
    vp._add_candidate(cands, seen, "X", "https://x.com/UCBerkeley", "serper",
                      {"serper_title": "UC Berkeley (@UCBerkeley) / X",
                       "serper_snippet": "Official UC Berkeley account"})

    assert len(cands) == 1                                  # still de-duplicated
    assert cands[0]["source"] == "input"                   # original provenance kept
    assert cands[0]["meta"]["serper_title"].startswith("UC Berkeley")
    assert vs._candidate_has_evidence(cands[0]) is True     # now clears the floor


def test_merge_never_overwrites_existing_evidence():
    cands: list = []
    seen: set = set()
    vp._add_candidate(cands, seen, "X", "https://x.com/UCBerkeley", "serper",
                      {"serper_title": "ORIGINAL"})
    vp._add_candidate(cands, seen, "X", "https://x.com/UCBerkeley", "input",
                      {"serper_title": "SHOULD NOT WIN", "supplied_in_client_record": True})
    assert cands[0]["meta"]["serper_title"] == "ORIGINAL"            # kept
    assert cands[0]["meta"]["supplied_in_client_record"] is True     # empty slot filled


def test_uncorroborated_client_handle_stays_without_evidence():
    # When Serper never returns the client handle, nothing merges — the content-
    # less input candidate still fails the floor and (correctly) stays Manual.
    cands: list = []
    seen: set = set()
    vp._add_candidate(cands, seen, "X", "https://x.com/UCBerkeley", "input",
                      {"supplied_in_client_record": True})
    assert vs._candidate_has_evidence(cands[0]) is False


# ── corroboration excludes deliberately-held cells from the rescue pass ───────
# A cell a guard HELD for a human (private account, surname mismatch, unconfirmed
# input, distributor channel, campus/parent mismatch) carries result.held=True and
# must never be re-promoted; only an evidence-shortfall Manual is rescued. This is
# decided from the held flag, not by re-detecting the condition inside the rescue.
from app.identity import wikipedia as _wiki              # noqa: E402


def _mr(url: str, status: str, platform: str, held: bool = False) -> "vp.VerificationResult":
    return vp.VerificationResult(
        platform=platform, best_candidate=url, status=status, held=held,
        confidence=60 if status == vp.STATUS_MANUAL else 95,
        decision="manual_review" if status == vp.STATUS_MANUAL else "verified",
    )


def _always_verified(platform, gt, cands, **k):
    return vp.VerificationResult(platform=platform, best_candidate=cands[0]["url"],
                                 status=vp.STATUS_VERIFIED, confidence=95, decision="verified")


def test_corroboration_excludes_a_held_private_instagram(monkeypatch):
    # A private client IG was deliberately held (held=True). Corroboration must not even
    # consider it for rescue — no re-detection of 'private' from an enrichment fetch. The
    # stubbed verifier would flip anything it rescues to Verified; the cell must stay put.
    monkeypatch.setattr(vp, "_drop_missing_profiles", lambda cands, platform: cands)
    monkeypatch.setattr(vp, "_enrich_candidates", lambda cands, platform: None)
    monkeypatch.setattr(vp.verification_service, "verify_platform", _always_verified)

    held_mr = _mr("https://www.instagram.com/client_private", vp.STATUS_MANUAL, "Instagram", held=True)
    results = {
        "Instagram": held_mr,
        "YouTube": _mr("https://www.youtube.com/@client", vp.STATUS_VERIFIED, "YouTube"),
    }
    out = vp._corroborate_row(
        "Jane Doe", _wiki.WikiMetadata(talent="Jane Doe", name="Jane Doe"), results, DEFAULT)
    assert out["Instagram"].status == vp.STATUS_MANUAL
    assert out["Instagram"] is held_mr          # untouched — never entered the rescue


def test_corroboration_excludes_a_held_campus_parent(monkeypatch):
    # A campus subject's parent-channel Manual is held (held=True) and must stay Manual,
    # not be promoted by corroboration.
    monkeypatch.setattr(vp, "_drop_missing_profiles", lambda cands, platform: cands)
    monkeypatch.setattr(vp, "_enrich_candidates", lambda cands, platform: None)
    monkeypatch.setattr(vp.verification_service, "verify_platform", _always_verified)

    held_mr = _mr("https://www.youtube.com/user/Rutgers", vp.STATUS_MANUAL, "YouTube", held=True)
    results = {
        "YouTube": held_mr,
        "Instagram": _mr("https://www.instagram.com/rutgerscamden", vp.STATUS_VERIFIED, "Instagram"),
    }
    out = vp._corroborate_row(
        "Rutgers University (Camden) - DAR",
        _wiki.WikiMetadata(talent="Rutgers University (Camden) - DAR",
                           name="Rutgers University (Camden) - DAR"),
        results, DEFAULT)
    assert out["YouTube"].status == vp.STATUS_MANUAL
    assert out["YouTube"] is held_mr


def test_corroboration_still_rescues_an_evidence_shortfall_cell(monkeypatch):
    # An ordinary evidence-shortfall Manual (held=False) — e.g. an X handle the LLM
    # accepted but the evidence floor downgraded — is STILL rescued and can be promoted
    # when another platform corroborates the row. Proves the exclusion didn't break the
    # intended rescue.
    monkeypatch.setattr(vp, "_drop_missing_profiles", lambda cands, platform: cands)
    monkeypatch.setattr(vp, "_enrich_candidates", lambda cands, platform: None)
    monkeypatch.setattr(vp.verification_service, "verify_platform", _always_verified)

    results = {
        "X": _mr("https://x.com/ucberkeley", vp.STATUS_MANUAL, "X"),               # held=False
        "Instagram": _mr("https://www.instagram.com/ucberkeley", vp.STATUS_VERIFIED, "Instagram"),
    }
    out = vp._corroborate_row(
        "UC Berkeley", _wiki.WikiMetadata(talent="UC Berkeley", name="UC Berkeley"), results, DEFAULT)
    assert out["X"].status == vp.STATUS_VERIFIED
