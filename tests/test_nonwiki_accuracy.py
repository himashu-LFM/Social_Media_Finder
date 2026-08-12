"""
Accuracy fixes for the non-Wikipedia population.

Every case here is a cell the pipeline actually got wrong on the first custom-mode
run (scraper_test.xlsx — Japanese and Thai indie musicians), traced to its cause.
"""
import apify_service as ap
import bio_link_service as bl
import search_options as so
import verification_pipeline as vp
import verification_service as vs

CUSTOM = so.SearchOptions(mode="custom", query_template="{name} site:{domain}")


# ── a band is talent ────────────────────────────────────────────────────────
# Pa-Om 99999's real YouTube channel — bio "ผ้าอ้อม99999…公式 … 4人組バンド",
# i.e. literally "official … 4-member band" — was labelled Wrong at confidence
# 30 because the model read "is a band" as contradicting title_category=Talent.
# Yasashii Mirai's Facebook was rejected for the same stated reason.

def test_the_prompt_states_that_a_group_is_not_a_contradiction():
    msg = vs._SYSTEM_MSG
    assert "A GROUP IS NOT A CONTRADICTION" in msg
    for word in ("bands", "collectives", "NOT a conflict"):
        assert word in msg


def test_the_prompt_accepts_native_script_names_as_matches():
    """อูโน่ หลาวทอง IS Uno Laothong; やさしいみらい IS Yasashii Mirai."""
    msg = vs._SYSTEM_MSG
    assert "transliteration of the input name, treat that as a MATCH" in msg


def test_the_prompt_recognises_officiality_markers_beyond_english():
    """The Pa-Om bio said 公式 — 'official' — and it counted for nothing."""
    for marker in ("公式", "공식", "官方", "ทางการ"):
        assert marker in vs._SYSTEM_MSG


# ── reusing a client-confirmed handle is evidence ───────────────────────────
# Meccha Bijin's Facebook and X, and Yuusari's Facebook/TikTok/X, all carried
# the exact handle the client had confirmed on Instagram. Every one of them
# landed in Manual Review at confidence 90-95, blocked by the evidence gate,
# because X and TikTok return no fetchable metadata.

def _cands(*urls):
    return [{"url": u, "meta": {}} for u in urls]


def test_a_reused_client_handle_is_tagged_as_evidence():
    cands = _cands("https://x.com/mettya_bizin")
    vp._tag_anchor_handle_matches(cands, ["mettya_bizin"], CUSTOM)
    assert cands[0]["meta"]["anchor_handle_match"] == "mettya_bizin"
    assert vs._has_strong_evidence(cands[0])


def test_the_tag_clears_the_evidence_gate_that_was_blocking_these_cells():
    cands = _cands("https://x.com/mettya_bizin")
    vp._tag_anchor_handle_matches(cands, ["mettya_bizin"], CUSTOM)
    # Before the tag this was "no substantive evidence" -> Manual Review.
    assert vs._thin_gate_block(vs.GATE_EVIDENCE, cands[0], 90) == ""


def test_a_short_handle_is_too_collidable_to_count():
    """'ampm' or 'uno' on another platform is coincidence, not corroboration."""
    cands = _cands("https://x.com/ampm")
    vp._tag_anchor_handle_matches(cands, ["ampm"], CUSTOM)
    assert "anchor_handle_match" not in cands[0]["meta"]


def test_a_different_handle_is_not_tagged():
    cands = _cands("https://x.com/someoneelse")
    vp._tag_anchor_handle_matches(cands, ["mettya_bizin"], CUSTOM)
    assert "anchor_handle_match" not in cands[0]["meta"]


def test_wikipedia_mode_does_not_get_the_looser_signal():
    """The tuned flow keeps its evidence bar exactly where it was."""
    cands = _cands("https://x.com/mettya_bizin")
    vp._tag_anchor_handle_matches(cands, ["mettya_bizin"], so.DEFAULT)
    assert "anchor_handle_match" not in cands[0]["meta"]


def test_the_tag_still_leaves_every_authenticity_guard_in_force():
    """A handle squatter must not ride in on the anchor match."""
    cand = {"url": "https://x.com/mettya_bizin",
            "meta": {"anchor_handle_match": "mettya_bizin",
                     "bio": "Fan page dedicated to Meccha Bijin"}}
    assert vs._authenticity_block("Meccha Bijin", {}, cand)


# ── Apify bios name the other platforms without writing full URLs ───────────
# Instagram publishes nothing to an anonymous fetch, so Apify's bio text is the
# only place these links exist. _collect_candidates only looked at strings
# starting with "http", which a bio almost never does.

def test_schemeless_links_in_an_apify_bio_become_candidates():
    items = [{"username": "kako",
              "biography": "音楽やってます  yt: youtube.com/@kakomusic / tiktok.com/@kako"}]
    found = ap._collect_candidates(items)
    assert found["YouTube"][0]["url"] == "https://www.youtube.com/@kakomusic"
    assert found["TikTok"][0]["url"].endswith("tiktok.com/@kako")
    assert found["YouTube"][0]["meta"]["found_in_profile_bio"] is True


def test_a_bio_with_no_links_adds_nothing():
    assert ap._collect_candidates([{"username": "k", "biography": "just vibes"}]) == {}


def test_bio_extraction_reuses_the_phase_zero_rules():
    """One extractor, so chrome filtering and redirect unwrapping can't drift."""
    items = [{"biography": "https://www.facebook.com/ig_xsite_user_info"}]
    assert ap._collect_candidates(items) == {}
    assert bl.links_by_platform("https://www.facebook.com/ig_xsite_user_info") == {}


# ── back-linking: the candidate names a profile the client gave us ──────────
# The client's Instagram handle is the one fact about a non-Wikipedia subject
# that no search engine supplied. A discovered YouTube channel that publishes
# that exact handle is agreeing with OUR record, not with its own claims.

CLIENT = {"Instagram": "https://www.instagram.com/mettya_bizin"}


def _yt(links):
    return [{"url": "https://www.youtube.com/@mettya_bizin",
             "meta": {"profile_links": links}}]


def test_a_candidate_that_links_back_is_tagged():
    cands = _yt({"Instagram": "https://www.instagram.com/mettya_bizin",
                 "X": "https://x.com/mettya_bizin"})
    vp._tag_backlinks(cands, CLIENT, CUSTOM)
    assert cands[0]["meta"]["backlink_to_client_profile"] == CLIENT["Instagram"]
    assert vs._has_strong_evidence(cands[0])


def test_a_backlink_to_someone_else_is_not_a_match():
    cands = _yt({"Instagram": "https://www.instagram.com/a_different_artist"})
    vp._tag_backlinks(cands, CLIENT, CUSTOM)
    assert "backlink_to_client_profile" not in cands[0]["meta"]


def test_url_form_differences_still_match():
    """http/https, www, trailing slash must not defeat the comparison."""
    cands = _yt({"Instagram": "http://instagram.com/mettya_bizin/"})
    vp._tag_backlinks(cands, CLIENT, CUSTOM)
    assert cands[0]["meta"].get("backlink_to_client_profile")


def test_wikipedia_mode_does_not_backlink():
    cands = _yt({"Instagram": CLIENT["Instagram"]})
    vp._tag_backlinks(cands, CLIENT, so.DEFAULT)
    assert "backlink_to_client_profile" not in cands[0]["meta"]


def _confirmed_yt(links, backlink=True):
    meta = {"profile_links": links}
    if backlink:
        meta["backlink_to_client_profile"] = CLIENT["Instagram"]
    return {"YouTube": vs.VerificationResult(
        platform="YouTube", best_candidate="https://www.youtube.com/@mettya_bizin",
        status=vs.STATUS_VERIFIED, confidence=95, source_meta=meta)}


def test_a_confirmed_backlinked_profile_vouches_for_its_other_links():
    results = _confirmed_yt({"Instagram": CLIENT["Instagram"],
                             "TikTok": "https://www.tiktok.com/@mettya_bizin",
                             "X": "https://x.com/mettya_bizin"})
    out = vp._adopt_backlink_discoveries("Meccha Bijin", results, CLIENT, {}, CUSTOM)
    assert out["TikTok"].status == vs.STATUS_VERIFIED
    assert out["X"].status == vs.STATUS_VERIFIED
    assert "first-party" in out["TikTok"].reason


def test_adoption_never_overwrites_a_platform_we_already_confirmed():
    results = _confirmed_yt({"TikTok": "https://www.tiktok.com/@from_the_bio"})
    results["TikTok"] = vs.VerificationResult(
        platform="TikTok", best_candidate="https://www.tiktok.com/@found_by_search",
        status=vs.STATUS_VERIFIED, confidence=95)
    out = vp._adopt_backlink_discoveries("Meccha Bijin", results, CLIENT, {}, CUSTOM)
    assert out["TikTok"].best_candidate.endswith("found_by_search")


def test_adoption_does_fill_a_cell_search_could_not():
    results = _confirmed_yt({"TikTok": "https://www.tiktok.com/@mettya_bizin"})
    results["TikTok"] = vs.VerificationResult(
        platform="TikTok", status=vs.STATUS_NOT_FOUND)
    out = vp._adopt_backlink_discoveries("Meccha Bijin", results, CLIENT, {}, CUSTOM)
    assert out["TikTok"].status == vs.STATUS_VERIFIED


def test_nothing_is_adopted_from_a_profile_that_did_not_link_back():
    """Without the back-link the chain of trust has no first link."""
    results = _confirmed_yt({"TikTok": "https://www.tiktok.com/@x"}, backlink=False)
    out = vp._adopt_backlink_discoveries("Meccha Bijin", results, CLIENT, {}, CUSTOM)
    assert "TikTok" not in out


def test_nothing_is_adopted_from_an_unconfirmed_profile():
    results = _confirmed_yt({"TikTok": "https://www.tiktok.com/@x"})
    results["YouTube"].status = vs.STATUS_MANUAL
    out = vp._adopt_backlink_discoveries("Meccha Bijin", results, CLIENT, {}, CUSTOM)
    assert "TikTok" not in out


def test_an_analyst_rejection_still_outranks_the_chain():
    results = _confirmed_yt({"TikTok": "https://www.tiktok.com/@mettya_bizin"})
    decisions = {"rejected": {"TikTok": "https://www.tiktok.com/@mettya_bizin"}}
    out = vp._adopt_backlink_discoveries("Meccha Bijin", results, CLIENT, decisions, CUSTOM)
    assert "TikTok" not in out


def test_wikipedia_mode_adopts_nothing():
    results = _confirmed_yt({"TikTok": "https://www.tiktok.com/@mettya_bizin"})
    assert vp._adopt_backlink_discoveries(
        "Meccha Bijin", results, CLIENT, {}, so.DEFAULT) == results
