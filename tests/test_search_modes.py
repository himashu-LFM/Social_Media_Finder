"""
Wikipedia mode vs custom (non-Wikipedia) mode.

The load-bearing test in this file is the first one: the user's constraint was
"don't hamper existing wikipedia flow accuracy and working", so the default path
must render the same query string and apply the same gate it did before custom
mode existed. Everything else here describes the new mode.
"""
import pytest

import search_options as so
import serper_service as ss
import verification_service as vs


# ── the default path must not have moved ────────────────────────────────────

def test_wikipedia_mode_renders_the_original_query():
    assert so.DEFAULT.template == ""
    assert (ss.build_query(ss.DEFAULT_QUERY_TEMPLATE, "Guy Branum", "Instagram",
                           "instagram.com") == "Guy Branum site:instagram.com")


def test_wikipedia_mode_keeps_the_strict_thin_gate():
    assert so.DEFAULT.thin_gate == vs.GATE_STRICT
    assert vs._thin_gate_block(vs.GATE_STRICT, _rich_candidate(), 100)


def test_unknown_mode_falls_back_to_wikipedia():
    """A typo must not silently hand an analyst a differently-gated run."""
    for mode in ("", "CUSTOM ", "wikipedia", "spotify", None):
        opts = so.from_request(mode, "{name} anything")
        if (mode or "").strip().lower() == "custom":
            continue
        assert opts.mode == so.MODE_WIKIPEDIA
        assert opts.thin_gate == vs.GATE_STRICT


# ── query templating ────────────────────────────────────────────────────────

@pytest.mark.parametrize("template,expected", [
    ("{name} site:{domain}", "Kako site:instagram.com"),
    ("{name} {subcategory} official {platform}", "Kako Musician official Instagram"),
    ('"{name}" {category}', '"Kako" Talent'),
    ("{name} site:{domain} -fan -parody", "Kako site:instagram.com -fan -parody"),
])
def test_templates_render(template, expected):
    assert ss.build_query(template, "Kako", "Instagram", "instagram.com",
                          "Talent", "Musician") == expected


def test_unknown_placeholder_is_left_literal_not_an_error():
    """A typo should produce an odd search, never a failed run."""
    assert ss.build_query("{name} {nope}", "Kako", "Instagram", "instagram.com") \
        == "Kako {nope}"


def test_missing_taxonomy_collapses_whitespace():
    assert ss.build_query("{name} {category} {subcategory} x", "Kako",
                          "Instagram", "instagram.com") == "Kako x"


@pytest.mark.parametrize("template,ok", [
    ("{name} site:{domain}", True),
    ("", False),
    ("site:instagram.com", False),   # no {name} — every row searches the same thing
    ("{name} " + "x" * 400, False),
])
def test_template_validation(template, ok):
    assert (so.validate_template(template) == "") is ok


# ── the evidence gate ───────────────────────────────────────────────────────

def _rich_candidate():
    return {"url": "https://www.instagram.com/kako",
            "meta": {"bio": "Producer. Tokyo.", "followers": "412K"}}


def _bare_candidate():
    return {"url": "https://www.instagram.com/kako", "meta": {}}


def test_custom_mode_confirms_a_well_evidenced_profile():
    assert so.SearchOptions(mode="custom").thin_gate == vs.GATE_EVIDENCE
    assert vs._thin_gate_block(vs.GATE_EVIDENCE, _rich_candidate(), 95) == ""


def test_custom_mode_still_escalates_a_bare_profile():
    """Relaxing the gate must not mean accepting a URL that parsed and nothing else."""
    assert vs._thin_gate_block(vs.GATE_EVIDENCE, _bare_candidate(), 99)


def test_custom_mode_still_escalates_a_marginal_verdict():
    assert vs._thin_gate_block(vs.GATE_EVIDENCE, _rich_candidate(), 80)


def test_custom_mode_escalates_when_no_candidate_was_chosen():
    assert vs._thin_gate_block(vs.GATE_EVIDENCE, None, 100)


def test_a_subject_with_real_ground_truth_never_reaches_the_gate():
    """Mode is irrelevant when Wikipedia/Wikidata actually returned facts."""
    assert not vs._ground_truth_is_thin({"professions": ["comedian"]})


# ── template and mode validation ────────────────────────────────────────────
# A run costs one Serper call per row per platform, so a typo has to be caught
# before the run starts rather than discovered in the results.

def test_unknown_placeholder_is_rejected():
    """A typo would otherwise appear verbatim in every query in the file."""
    problem = so.validate_template("{name} {platfrom} official")
    assert "{platfrom}" in problem


def test_the_rejection_lists_the_placeholders_that_do_exist():
    problem = so.validate_template("{name} {platfrom}")
    assert "{platform}" in problem and "{subcategory}" in problem


def test_every_documented_placeholder_is_accepted():
    template = " ".join("{" + f + "}" for f in ss.TEMPLATE_FIELDS)
    assert so.validate_template(template) == ""


def test_validation_covers_exactly_what_build_query_renders():
    """If the two ever drift, a valid template starts emitting literal braces."""
    rendered = ss.build_query(
        " ".join("{" + f + "}" for f in ss.TEMPLATE_FIELDS),
        "Kako", "Instagram", "instagram.com", "Music", "Producer")
    assert "{" not in rendered


def test_an_unrecognised_mode_is_reported_not_absorbed():
    assert so.validate_mode("banana")


def test_saying_nothing_is_wikipedia_mode_and_always_valid():
    assert so.validate_mode("") == ""
    assert so.validate_mode("wikipedia") == "" and so.validate_mode("custom") == ""
