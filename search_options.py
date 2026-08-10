"""
search_options.py  —  how a run should search

Two modes, one code path.

**Wikipedia mode** (the default, and what every existing caller gets by passing
nothing) is the flow that has been tuned and measured: Wikipedia/Wikidata ground
truth, a ``"<name> site:<domain>"`` Serper query, and the strict thin-ground-truth
gate that refuses to Verify a name-only match.

**Custom mode** is for the populations that have no Wikipedia page — the majority
of a typical client file. The analyst writes the Serper query themselves, because
they know what disambiguates their list (a label, a sport, a show, a city) far
better than a generic template does.

The important property is that these are *not* two pipelines. Custom mode changes
exactly two things — the query string and the thin-ground-truth gate — and shares
every guard, every enrichment step and every adjudication prompt with Wikipedia
mode. There is no second code path to drift out of sync, and a run that passes no
options is byte-for-byte the run that shipped before this file existed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import serper_service
from serper_service import TEMPLATE_FIELDS

MODE_WIKIPEDIA = "wikipedia"
MODE_CUSTOM = "custom"

# What the thin-ground-truth gate does when the subject has no identity facts:
#   "strict"   — never Verify (Wikipedia mode; unchanged behaviour)
#   "evidence" — Verify only on strong evidence + high confidence (custom mode)
GATE_STRICT = "strict"
GATE_EVIDENCE = "evidence"


@dataclass(frozen=True)
class SearchOptions:
    """Per-run search configuration. The default instance is Wikipedia mode."""

    mode: str = MODE_WIKIPEDIA
    query_template: str = ""

    @property
    def is_custom(self) -> bool:
        return self.mode == MODE_CUSTOM

    @property
    def template(self) -> str:
        """The template Serper should render. Empty in Wikipedia mode."""
        return self.query_template.strip() if self.is_custom else ""

    @property
    def thin_gate(self) -> str:
        """
        Custom mode exists precisely for subjects Wikipedia does not cover, so
        the strict gate would reject every result it is asked to produce. It is
        replaced by an evidence-based gate, not removed — see
        ``verification_service`` for what that requires.
        """
        return GATE_EVIDENCE if self.is_custom else GATE_STRICT


DEFAULT = SearchOptions()


def from_request(mode: str = "", query_template: str = "") -> SearchOptions:
    """
    Build options from untrusted input (an API body, a form field).

    Anything unrecognised falls back to Wikipedia mode: a typo in the mode name
    must not silently hand the analyst a differently-gated run.
    """
    mode = (mode or "").strip().lower()
    if mode != MODE_CUSTOM:
        return DEFAULT
    return SearchOptions(mode=MODE_CUSTOM, query_template=(query_template or "").strip())


def validate_mode(mode: str) -> str:
    """
    Return a problem with an explicitly-supplied mode, or "" if it is usable.

    Empty means "caller said nothing", which is Wikipedia mode and always fine.
    A non-empty value we do not recognise is a typo, and it is reported rather
    than absorbed: silently running Wikipedia mode for someone who asked for
    custom is exactly the surprise ``from_request``'s fallback is meant to avoid.
    """
    mode = (mode or "").strip().lower()
    if not mode or mode in (MODE_WIKIPEDIA, MODE_CUSTOM):
        return ""
    return f"Unknown search mode {mode!r}. Use {MODE_WIKIPEDIA!r} or {MODE_CUSTOM!r}."


def validate_template(template: str) -> str:
    """
    Return a human-readable problem with the template, or "" if it is usable.

    Catches the mistakes that would waste a whole run's Serper budget. An
    unrecognised placeholder counts: ``build_query`` leaves it as literal text,
    so a typo like ``{platfrom}`` puts that brace-wrapped string into every
    single query and quietly degrades the entire run's results.
    """
    template = (template or "").strip()
    if not template:
        return "Enter a search query, or switch back to Wikipedia mode."
    if "{name}" not in template:
        return "The query must include {name}, or every row would search for the same thing."
    if len(template) > 300:
        return "That query is too long for a Google search."
    unknown = [f"{{{f}}}" for f in re.findall(r"\{([^{}]*)\}", template)
               if f not in TEMPLATE_FIELDS]
    if unknown:
        return (f"Unknown placeholder{'s' if len(unknown) > 1 else ''} "
                f"{', '.join(sorted(set(unknown)))}. Available: "
                f"{', '.join('{' + f + '}' for f in TEMPLATE_FIELDS)}.")
    return ""


def preview(template: str, example_name: str = "Virat Kohli",
            platform: str = "Instagram", category: str = "Talent",
            subcategory: str = "Athlete") -> str:
    """Render the template exactly as Serper will see it, for the UI preview."""
    return serper_service.build_query(
        template or serper_service.DEFAULT_QUERY_TEMPLATE,
        example_name, platform, "instagram.com", category, subcategory,
    )
