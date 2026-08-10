"""
Authenticity and safety guards.

These pin the rules that decide whether a candidate may be labelled Verified.
Every case here is a real profile the pipeline got wrong at some point — the
comment on each says which. All guards are one-directional: they may only
downgrade Verified to Manual Review, so a regression shows up as a false
positive escaping, never as a correct answer being suppressed.
"""
import verification_service as vs

NOTABLE = {"name": "X", "known_works": ["A Work"], "summary": "An actor"}


def cand(url, **meta):
    return {"url": url, "source": "serper", "meta": meta}


# ── third-party framing ─────────────────────────────────────────────────────

def test_blocks_page_that_declares_itself_about_someone():
    """Tamara Taylor: a fan page stating every fact correctly, but about her."""
    c = cand("https://www.facebook.com/TamaraTaylortheactress",
             serper_snippet="Tamara Taylor. 5,623 likes. This page is about Tamara Taylor, "
                            "a canadian actress. Famous for Dr.Cam Saroyan role in Bones")
    assert "third party" in vs._authenticity_block("Tamara Taylor", NOTABLE, c)


def test_blocks_unofficial_page():
    """Brendan Coyle: 'Unofficial page for the actor Brendan Coyle'."""
    c = cand("https://www.facebook.com/BrendanCoyleOnline",
             serper_snippet="Unofficial page for the actor Brendan Coyle.")
    assert vs._authenticity_block("Brendan Coyle", NOTABLE, c)


def test_allows_official_page_written_in_third_person():
    """Guy Branum's real page is third-person. Third person alone must not block."""
    c = cand("https://www.facebook.com/guy.branumcomedian", display_name="Guy Branum",
             serper_snippet="Guy Branum is a comedian best known for his work on Chelsea Lately",
             followers="45K")
    assert vs._authenticity_block("Guy Branum", NOTABLE, c) == ""


# ── name-order mismatch ─────────────────────────────────────────────────────

def test_blocks_reordered_name():
    """James Scully: 'Scully N James' passes a token-set match but is someone else."""
    c = cand("https://x.com/scullynjames", display_name="Scully N James")
    assert "different order" in vs._authenticity_block("James Scully", NOTABLE, c)


def test_allows_inserted_middle_name():
    """Anthony Edwards the actor is 'Anthony Charles Edwards' — insertion is fine."""
    c = cand("https://www.facebook.com/anthonyedwardsactor",
             display_name="Anthony Charles Edwards", followers="1,150")
    assert vs._authenticity_block("Anthony Edwards", NOTABLE, c) == ""


def test_allows_parenthetical_suffix():
    c = cand("https://www.instagram.com/tobykebbelll",
             display_name="Toby Kebbell (actor)", followers="15K")
    assert vs._authenticity_block("Toby Kebbell", NOTABLE, c) == ""


# ── follower plausibility ───────────────────────────────────────────────────

def test_blocks_implausible_follower_count_for_notable_subject():
    """Marcus Rutherford: 145 followers for a working actor."""
    c = cand("https://www.instagram.com/marcusalanrutherford",
             display_name="Marcus Rutherford", followers="145")
    assert "implausible" in vs._authenticity_block("Marcus Rutherford", NOTABLE, c)


def test_ignores_follower_count_for_unknown_subject():
    """Thin ground truth means we cannot judge what a plausible following is."""
    c = cand("https://www.instagram.com/somebody", display_name="Some Body", followers="145")
    assert vs._authenticity_block("Some Body", {"name": "Some Body"}, c) == ""


def test_k_suffix_is_above_the_floor():
    c = cand("https://www.instagram.com/x", display_name="X Y", followers="15K")
    assert vs._authenticity_block("X Y", NOTABLE, c) == ""


# ── fan-handle shape ────────────────────────────────────────────────────────

def test_blocks_trailing_digit_handle_without_evidence():
    """Peter Claffey: @peter_claffey1 quoting his real role."""
    c = cand("https://www.tiktok.com/@peter_claffey1",
             serper_snippet="Ser Duncan the Tall in A Knight of the Seven Kingdoms")
    assert vs._authenticity_block("Peter Claffey", NOTABLE, c)


def test_allows_digit_handle_when_real_evidence_exists():
    """A digit alone is not disqualifying when the profile itself backs it up."""
    c = cand("https://www.instagram.com/peter_claffey1", display_name="Peter Claffey",
             followers="357K", bio="Actor. Ser Duncan the Tall.")
    assert vs._authenticity_block("Peter Claffey", NOTABLE, c) == ""


# ── evidence floor ──────────────────────────────────────────────────────────

def test_username_alone_is_not_evidence():
    """username is derived from the URL, so it proves nothing about the account."""
    assert not vs._candidate_has_evidence(cand("https://x.com/a", username="a"))


def test_snippet_counts_as_evidence():
    assert vs._candidate_has_evidence(cand("https://x.com/a", serper_snippet="Some text"))


# ── thin ground truth ───────────────────────────────────────────────────────

def test_person_with_name_only_is_thin():
    assert vs._ground_truth_is_thin(
        {"name": "Adam Grissom", "entity_type": "person",
         "provided_metadata": {"title_category": "Talent"}})


def test_organisation_with_metadata_is_not_thin():
    """Brand names are near-unique, so a category disambiguates where it can't for people."""
    assert not vs._ground_truth_is_thin(
        {"name": "NetBrain", "entity_type": "Internet Services",
         "provided_metadata": {"title_category": "Internet Services"}})
