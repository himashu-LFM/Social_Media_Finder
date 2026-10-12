"""
Ground-truth resolution must not attach a STRANGER'S biography to a row.

A provided Wikipedia URL / IMDb id is trusted to be the right person, but the
input file can be wrong — it listed Damon Dash's page for his son 'Boogie Dash',
and the whole row was then verified against a music executive (a namesake TikTok
'matched'). The name-search path already rejects a name mismatch; the provided-
link paths must do the same.
"""
from app.identity import wikipedia as wk
from app.identity import wikidata as wd


def _entity(label, aliases=()):
    return {"labels": {"en": {"value": label}},
            "aliases": {"en": [{"value": a} for a in aliases]},
            "claims": {}}


def test_provided_wiki_url_pointing_at_the_wrong_person_is_rejected(monkeypatch):
    wk._METADATA_CACHE.clear()
    monkeypatch.setattr(wd, "_wikipedia_url_to_qid", lambda url: "Q344384")
    monkeypatch.setattr(wd, "_fetch_wikidata_entity",
                        lambda qid: _entity("Damon Dash", ["Dame Dash"]))
    m = wk.fetch_wiki_metadata("Boogie Dash", "https://en.wikipedia.org/wiki/Damon_Dash")
    assert m.name == "Boogie Dash"     # the stranger's name was NOT adopted
    assert not m.professions           # nor his biography
    assert m.found is False            # falls back to name-only ground truth


def test_provided_imdb_id_pointing_at_the_wrong_person_is_rejected(monkeypatch):
    wk._METADATA_CACHE.clear()
    monkeypatch.setattr(wd, "_wikipedia_url_to_qid", lambda url: None)
    monkeypatch.setattr(wd, "_imdb_to_qid", lambda imdb: "Q344384")
    monkeypatch.setattr(wd, "_fetch_wikidata_entity",
                        lambda qid: _entity("Damon Dash", ["Dame Dash"]))
    m = wk.fetch_wiki_metadata("Boogie Dash", "", input_metadata={"imdb_id": "nm0000111"})
    assert m.name == "Boogie Dash"
    assert m.found is False


def test_provided_wiki_url_matching_the_talent_is_adopted(monkeypatch):
    wk._METADATA_CACHE.clear()
    monkeypatch.setattr(wd, "_wikipedia_url_to_qid", lambda url: "Q999")
    monkeypatch.setattr(wd, "_fetch_wikidata_entity",
                        lambda qid: _entity("Boogie Dash"))
    m = wk.fetch_wiki_metadata("Boogie Dash", "https://en.wikipedia.org/wiki/Boogie_Dash")
    assert m.qid == "Q999"             # the matching entity WAS adopted


def test_a_stage_name_alias_still_resolves(monkeypatch):
    """The talent name need only match the label OR an alias, so a stage name on
    the entity's legal-name label still resolves."""
    wk._METADATA_CACHE.clear()
    monkeypatch.setattr(wd, "_wikipedia_url_to_qid", lambda url: "Q76")
    monkeypatch.setattr(wd, "_fetch_wikidata_entity",
                        lambda qid: _entity("Stefani Germanotta", ["Lady Gaga"]))
    m = wk.fetch_wiki_metadata("Lady Gaga", "https://en.wikipedia.org/wiki/Lady_Gaga")
    assert m.qid == "Q76"             # matched via the alias, not discarded
