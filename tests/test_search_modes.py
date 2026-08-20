"""
Wikipedia mode vs custom (non-Wikipedia) mode.

The load-bearing test in this file is the first one: the user's constraint was
"don't hamper existing wikipedia flow accuracy and working", so the default path
must render the same query string and apply the same gate it did before custom
mode existed.

Custom mode itself was redesigned to the SerpApi Google-AI-Mode approach: the
analyst supplies a free-text ``prompt`` and a profession toggle (not a Serper
query template), and the query built per talent is "<name> [<profession>]
<prompt>". Serper's own ``build_query`` / template machinery is retained and
still tested here because the WIKIPEDIA flow continues to use it.
"""
import pytest

import search_options as so
import serper_service as ss
import verification_service as vs


# ── the default (Wikipedia) path must not have moved ────────────────────────

def test_wikipedia_mode_renders_the_original_query():
    assert so.DEFAULT.template == ""
    assert (ss.build_query(ss.DEFAULT_QUERY_TEMPLATE, "Guy Branum", "Instagram",
                           "instagram.com") == "Guy Branum site:instagram.com")


def test_wikipedia_mode_keeps_the_strict_thin_gate():
    assert so.DEFAULT.thin_gate == vs.GATE_STRICT
    assert vs._thin_gate_block(vs.GATE_STRICT, _rich_candidate(), 100)


def test_unknown_mode_falls_back_to_wikipedia():
    """A typo must not silently hand an analyst a differently-gated run."""
    for mode in ("", "wikipedia", "spotify", None):
        opts = so.from_request(mode, "social media handles")
        assert opts.mode == so.MODE_WIKIPEDIA
        assert opts.thin_gate == vs.GATE_STRICT


# ── Serper query templating (Wikipedia flow — build_query is unchanged) ──────

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


# ── custom mode: prompt + profession toggle (the SerpApi approach) ──────────

def test_custom_mode_carries_prompt_and_profession_flag():
    opts = so.from_request("custom", "  social media handles  ", True)
    assert opts.is_custom
    assert opts.prompt == "social media handles"       # trimmed
    assert opts.include_profession is True
    # Custom mode does not drive a Serper template.
    assert opts.template == ""


def test_custom_mode_profession_can_be_switched_off():
    opts = so.from_request("custom", "official accounts", False)
    assert opts.include_profession is False


def test_custom_mode_accepts_an_empty_prompt():
    """An empty prompt is valid — the query is just "<name> <profession>"."""
    opts = so.from_request("custom", "", True)
    assert opts.is_custom and opts.prompt == ""
    assert so.validate_prompt("") == ""


def test_a_too_long_prompt_is_rejected():
    assert so.validate_prompt("x" * 400)


# ── the evidence gate (Wikipedia-flow verification is unchanged) ────────────

def _rich_candidate():
    return {"url": "https://www.instagram.com/kako",
            "meta": {"bio": "Producer. Tokyo.", "followers": "412K"}}


def _bare_candidate():
    return {"url": "https://www.instagram.com/kako", "meta": {}}


def test_custom_mode_thin_gate_is_evidence_based():
    assert so.SearchOptions(mode="custom").thin_gate == vs.GATE_EVIDENCE
    assert vs._thin_gate_block(vs.GATE_EVIDENCE, _rich_candidate(), 95) == ""


def test_evidence_gate_still_escalates_a_bare_profile():
    """Relaxing the gate must not mean accepting a URL that parsed and nothing else."""
    assert vs._thin_gate_block(vs.GATE_EVIDENCE, _bare_candidate(), 99)


def test_evidence_gate_still_escalates_a_marginal_verdict():
    assert vs._thin_gate_block(vs.GATE_EVIDENCE, _rich_candidate(), 80)


def test_a_subject_with_real_ground_truth_never_reaches_the_gate():
    """Mode is irrelevant when Wikipedia/Wikidata actually returned facts."""
    assert not vs._ground_truth_is_thin({"professions": ["comedian"]})


# ── mode validation ─────────────────────────────────────────────────────────

def test_an_unrecognised_mode_is_reported_not_absorbed():
    assert so.validate_mode("banana")


def test_saying_nothing_is_wikipedia_mode_and_always_valid():
    assert so.validate_mode("") == ""
    assert so.validate_mode("wikipedia") == "" and so.validate_mode("custom") == ""
