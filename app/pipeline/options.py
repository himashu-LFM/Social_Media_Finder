"""
search_options.py  —  how a run should search

Two modes.

**Wikipedia mode** (the default, and what every existing caller gets by passing
nothing) is the tuned, measured flow: Wikipedia/Wikidata ground truth, a
``"<name> site:<domain>"`` Serper query, LLM verification, and the strict
thin-ground-truth gate that refuses to Verify a name-only match.

**Custom mode** is for the populations that have no Wikipedia page. It does NOT
run Serper/LLM/Apify. Instead:

    Phase 0  — first-party bio links from the client's own Instagram/YouTube
               handle (adopted Verified — the account holder published them).
    SerpApi  — for every platform Phase 0 did not fill, one SerpApi Google-AI-Mode
               query "<name> [<profession>] <prompt>" returns the links Google
               cites, tagged Manual Review Needed (no LLM).

The analyst supplies a free-text ``prompt`` (e.g. "social media handles") and
chooses whether the profession pulled from the file is included in the query.
``name`` and ``profession`` come from the spreadsheet; only the prompt is typed.
"""

from __future__ import annotations

from dataclasses import dataclass

MODE_WIKIPEDIA = "wikipedia"
MODE_CUSTOM = "custom"

# What the thin-ground-truth gate does when the subject has no identity facts.
# Retained for the Wikipedia flow; custom mode does not verify with the LLM.
GATE_STRICT = "strict"
GATE_EVIDENCE = "evidence"


@dataclass(frozen=True)
class SearchOptions:
    """Per-run search configuration. The default instance is Wikipedia mode."""

    mode: str = MODE_WIKIPEDIA
    # Custom-mode SerpApi query suffix — the analyst's free-text prompt, appended
    # after "<name> <profession>". Empty is valid (just name + profession).
    prompt: str = ""
    # Whether the file's profession/category is included in the custom query.
    include_profession: bool = True

    @property
    def is_custom(self) -> bool:
        return self.mode == MODE_CUSTOM

    @property
    def template(self) -> str:
        """
        Serper query template. Always empty here — Wikipedia mode uses Serper's
        own default ("<name> site:<domain>"), and custom mode does not use Serper
        at all. Kept so the Wikipedia discovery path can read it unconditionally.
        """
        return ""

    @property
    def thin_gate(self) -> str:
        return GATE_EVIDENCE if self.is_custom else GATE_STRICT


DEFAULT = SearchOptions()


def from_request(mode: str = "", prompt: str = "",
                 include_profession: bool = True) -> SearchOptions:
    """
    Build options from untrusted input (an API body, a form field).

    Anything unrecognised falls back to Wikipedia mode: a typo in the mode name
    must not silently hand the analyst a differently-behaving run.
    """
    mode = (mode or "").strip().lower()
    if mode != MODE_CUSTOM:
        return DEFAULT
    return SearchOptions(
        mode=MODE_CUSTOM,
        prompt=(prompt or "").strip(),
        include_profession=bool(include_profession),
    )


def validate_mode(mode: str) -> str:
    """
    Return a problem with an explicitly-supplied mode, or "" if it is usable.

    Empty means "caller said nothing", which is Wikipedia mode and always fine.
    A non-empty value we do not recognise is a typo, and it is reported rather
    than absorbed.
    """
    mode = (mode or "").strip().lower()
    if not mode or mode in (MODE_WIKIPEDIA, MODE_CUSTOM):
        return ""
    return f"Unknown search mode {mode!r}. Use {MODE_WIKIPEDIA!r} or {MODE_CUSTOM!r}."


def validate_prompt(prompt: str) -> str:
    """
    Return a human-readable problem with the custom prompt, or "" if usable.

    The prompt is optional (an empty prompt just searches "<name> <profession>"),
    so the only real limit is length — Google truncates very long queries.
    """
    if (prompt or "").strip() and len(prompt.strip()) > 300:
        return "That prompt is too long for a Google search."
    return ""
