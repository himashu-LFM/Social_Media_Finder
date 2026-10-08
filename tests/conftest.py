"""
Shared test fixtures.

Input Instagram/YouTube handles are confirmed against the talent's name by reading
the profile (Apify for Instagram, an OpenGraph page read for YouTube). To keep the
suite offline and deterministic, this fixture stubs those *readers* so the real
`_read_input_profile` logic runs but makes no network call: Instagram's reader is
off and YouTube's read comes back empty-and-not-missing, which yields
(None, False) — "couldn't read, so trust the handle" — exactly the old behaviour.
Tests that exercise validation override the specific reader they need. It also
clears the per-run Apify cache so one test's mocked actor item never leaks.
"""
import pytest

from app.discovery import apify as _apify
from app.output import profile_metadata as _pm


@pytest.fixture(autouse=True)
def _offline_input_reads(monkeypatch):
    # Stub the lowest-level readers so no input-profile read touches the network:
    # the Apify actor returns nothing and YouTube's page read is empty & not-missing,
    # so _read_input_profile yields (None, False) = trust. Stubbing _run_named_actor
    # (not instagram_configured) leaves the apify tests free to configure the reader
    # and supply their own actor output.
    monkeypatch.setattr(_apify, "_run_named_actor", lambda actor_id, actor_input: [])
    monkeypatch.setattr(_pm, "fetch_profile_metadata", lambda url, platform: {})
    monkeypatch.setattr(_pm, "profile_is_missing", lambda url, platform: False)
    _apify.clear_cache()
    yield
    _apify.clear_cache()
